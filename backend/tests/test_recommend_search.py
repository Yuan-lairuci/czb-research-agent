"""模块四测试：双源检索合并、S2 降级路径、S2 限速/退避与搜索端点。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.recommend import create_recommend_router
from app.services.s2_client import S2Client, S2Error

S2_ITEM = {
    "paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
    "title": "Attention Is All You Need",
    "abstract": "The dominant sequence transduction models...",
    "year": 2017,
    "citationCount": 90000,
    "influentialCitationCount": 5000,
    "externalIds": {"ArXiv": "1706.03762", "DOI": "10.5555/3295222"},
    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
    "url": "https://www.semanticscholar.org/paper/x",
}
ARXIV_ITEM = {
    "arxiv_id": "1706.03762v7",
    "base_id": "1706.03762",
    "version": 7,
    "title": "Attention Is All You Need",
    "abstract": "The dominant sequence transduction models...",
    "published": "2017-06-12T17:57:34Z",
    "authors": ["Ashish Vaswani"],
    "pdf_url": "https://arxiv.org/pdf/1706.03762v7",
}
ARXIV_ONLY_ITEM = {
    "arxiv_id": "2301.00001v1",
    "base_id": "2301.00001",
    "version": 1,
    "title": "An arXiv-only Preprint",
    "abstract": "Abstract text",
    "published": "2023-01-01T00:00:00Z",
    "authors": ["Someone"],
    "pdf_url": "https://arxiv.org/pdf/2301.00001v1",
}


class StubS2:
    """零网络 S2 桩：可预置搜索结果或抛 S2Error 模拟不可达。"""

    def __init__(self, items=None, error=False):
        self.items = items if items is not None else []
        self.error = error

    def search(self, query, limit=10):
        if self.error:
            raise S2Error("s2 unreachable")
        return self.items[:limit]


class StubArxiv:
    """零网络 arXiv 桩。"""

    def __init__(self, items=None, error=False):
        self.items = items if items is not None else []
        self.error = error

    def search(self, query, max_results=10, field="all"):
        if self.error:
            from app.services.arxiv_client import ArxivError

            raise ArxivError("arxiv unreachable")
        return self.items[:max_results]


class FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.content = self.text.encode()
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


class FakeHTTP:
    """按序返回预置状态码的假 httpx.Client。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, params=None):
        self.calls += 1
        return self.responses.pop(0)


def test_search_merged_dual_source():
    """双源命中合并为 both，单源分别标 s2 / arxiv。"""
    from app.services.recommend import search_merged

    resp = search_merged(
        "attention",
        s2=StubS2([S2_ITEM]),
        arxiv=StubArxiv([ARXIV_ITEM, ARXIV_ONLY_ITEM]),
    )
    assert resp.degraded is False
    assert resp.s2_ok and resp.arxiv_ok
    by_arxiv = {c.arxiv_id: c for c in resp.results}
    assert by_arxiv["1706.03762"].source == "both"
    assert by_arxiv["1706.03762"].citation_count == 90000
    assert by_arxiv["2301.00001"].source == "arxiv"


def test_search_merged_marks_s2_only():
    """仅 S2 命中的条目标 source=s2。"""
    from app.services.recommend import search_merged

    only_s2 = dict(S2_ITEM, paperId="other-s2-id", externalIds={}, title="S2-only Paper")
    resp = search_merged("q", s2=StubS2([only_s2]), arxiv=StubArxiv([]))
    assert [c.source for c in resp.results] == ["s2"]
    assert resp.results[0].title == "S2-only Paper"


def test_search_merged_degrades_when_s2_down():
    """S2 不可达：只用 arXiv 照常返回并标 source=arxiv_only，不抛异常。"""
    from app.services.recommend import search_merged

    resp = search_merged(
        "attention",
        s2=StubS2(error=True),
        arxiv=StubArxiv([ARXIV_ITEM]),
    )
    assert resp.degraded is True
    assert resp.s2_ok is False
    assert resp.arxiv_ok is True
    assert len(resp.results) == 1
    assert resp.results[0].source == "arxiv_only"
    assert resp.results[0].arxiv_id == "1706.03762"


def test_search_endpoint_via_api(tmp_path):
    """POST /api/recommend/search 走注入桩返回 200。"""
    app = FastAPI()
    app.include_router(
        create_recommend_router(
            data_dir=tmp_path,
            s2=StubS2([S2_ITEM]),
            arxiv=StubArxiv([ARXIV_ITEM]),
        )
    )
    client = TestClient(app)
    resp = client.post("/api/recommend/search", json={"query": "attention", "limit": 5})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is False
    assert body["results"][0]["source"] == "both"


def test_s2_client_429_backoff_and_retry():
    """429 后指数退避重试成功；sleep_fn 记录退避时长。"""
    sleeps = []
    http = FakeHTTP(
        [
            FakeResp(429, {"message": "too many requests"}),
            FakeResp(200, {"data": [{"paperId": "p1"}]}),
        ]
    )
    s2 = S2Client(client=http, min_interval=0, sleep_fn=sleeps.append, max_retries=2)
    results = s2.search("attention")
    assert [r["paperId"] for r in results] == ["p1"]
    assert http.calls == 2
    assert sleeps == [1.0]


def test_s2_client_404_returns_none():
    """404 -> None（论文不存在），不抛异常。"""
    s2 = S2Client(client=FakeHTTP([FakeResp(404)]), min_interval=0, sleep_fn=lambda s: None)
    assert s2.get_paper("arxiv:0000.00000") is None
