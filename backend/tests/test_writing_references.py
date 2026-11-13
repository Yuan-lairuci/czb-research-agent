"""参考文献 API 测试：反幻觉核对、降级与语料来源（零网络）。"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import writing
from app.services.chat_service import ChatAPIError
from app.services.writing_references import ReferencesEngine


class StubChat:
    """固定回复或固定抛错的 ChatService 桩。"""

    def __init__(self, reply=None, error=None) -> None:
        self.reply = reply
        self.error = error

    async def chat(self, messages, **kwargs):
        if self.error is not None:
            raise self.error
        return self.reply


class StubS2:
    """S2Client 桩：返回 canned records。"""

    def __init__(self, search_records=None, ref_edges=None) -> None:
        self.search_records = search_records or []
        self.ref_edges = ref_edges or []

    def search(self, query, limit=10):
        return self.search_records[:limit]

    def get_references(self, paper_id, limit=100):
        return self.ref_edges[:limit]

    def get_paper(self, paper_id):
        return None

    def close(self):
        return None


class StubArxiv:
    """ArxivClient 桩：返回 canned records。"""

    def __init__(self, records=None) -> None:
        self.records = records or []

    def search(self, query, max_results=10, field="all"):
        return self.records[:max_results]

    def get_paper(self, arxiv_id):
        return None

    def close(self):
        return None


S2_RECORD = {
    "title": "Layer normalization",
    "abstract": "",
    "year": 2016,
    "citationCount": 100,
    "influentialCitationCount": 10,
    "externalIds": {"ArXiv": "1607.06450", "DOI": "10.48550/arXiv.1607.06450"},
    "openAccessPdf": None,
    "url": "https://www.semanticscholar.org/paper/layer-norm",
    "authors": [
        {"authorId": "1", "name": "Jimmy Lei Ba"},
        {"authorId": "2", "name": "Geoffrey E Hinton"},
    ],
}

ARXIV_RECORD = {
    "arxiv_id": "1706.03762v7",
    "base_id": "1706.03762",
    "version": 7,
    "title": "Attention Is All You Need",
    "abstract": "",
    "published": "2017-06-12T00:00:00Z",
    "authors": ["Ashish Vaswani"],
    "pdf_url": "https://arxiv.org/pdf/1706.03762v7",
}


def make_client(chat, s2=None, arxiv=None, parsed_dir=None) -> TestClient:
    engine = ReferencesEngine(
        chat=chat,
        s2=s2 or StubS2(),
        arxiv=arxiv or StubArxiv(),
        parsed_dir=parsed_dir,
    )
    writing.set_references_engine(engine)
    app = FastAPI()
    app.include_router(writing.router)
    return TestClient(app)


def test_hallucinated_arxiv_id_dropped():
    """假 arxiv_id 必落 dropped 且注明原因；真条目内容整体来自来源记录。"""
    entries = [
        {
            "title": "Layer normalization",
            "authors": ["Fake Author"],
            "year": 2020,
            "venue": "FakeConf",
            "arxiv_id": "1607.06450",
            "doi": None,
            "url": "https://fake.example.com",
            "formatted": "",
        },
        {
            "title": "Fake Paper Title",
            "authors": ["Nobody"],
            "year": 2021,
            "venue": None,
            "arxiv_id": "9999.99999",
            "doi": None,
            "url": "https://fake.example.org",
            "formatted": "",
        },
    ]
    chat = StubChat(reply=json.dumps(entries, ensure_ascii=False))
    client = make_client(chat, s2=StubS2(search_records=[S2_RECORD]))
    resp = client.post("/api/writing/references", json={"topic": "attention mechanism"})
    assert resp.status_code == 200
    data = resp.json()
    dropped = [d for d in data["dropped"] if d["arxiv_id"] == "9999.99999"]
    assert dropped, "假 arxiv_id 未落 dropped"
    assert "查无此 id" in dropped[0]["reason"]
    refs = data["references"]
    assert len(refs) == 1
    ref = refs[0]
    assert ref["title"] == "Layer normalization"
    assert ref["year"] == 2016
    assert ref["authors"] == ["Jimmy Lei Ba", "Geoffrey E Hinton"]
    assert ref["arxiv_id"] == "1607.06450"
    assert ref["url"]
    assert ref["formatted"]
    assert data["sources_used"]


def test_title_mismatch_dropped():
    """核上 id 但题名对不上 → dropped 注明原因。"""
    entries = [{"title": "Attention Is All You Need", "arxiv_id": "1607.06450"}]
    chat = StubChat(reply=json.dumps(entries, ensure_ascii=False))
    client = make_client(chat, s2=StubS2(search_records=[S2_RECORD]))
    resp = client.post("/api/writing/references", json={"topic": "attention mechanism"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["references"] == []
    assert len(data["dropped"]) == 1
    assert "题名对不上" in data["dropped"][0]["reason"]


def test_llm_unavailable_falls_back_to_sources():
    """LLM 不可用 → 确定性降级，条目仍带 id/url。"""
    chat = StubChat(error=ChatAPIError("bad key"))
    client = make_client(
        chat,
        s2=StubS2(search_records=[S2_RECORD]),
        arxiv=StubArxiv(records=[ARXIV_RECORD]),
    )
    resp = client.post("/api/writing/references", json={"topic": "attention mechanism"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["references"]) >= 2
    for ref in data["references"]:
        assert ref["arxiv_id"] or ref["url"]
        assert ref["formatted"]


def test_corpus_paper_ids_source_prefix(tmp_path):
    """paper_ids 走语料 references，source 前缀 corpus:。"""
    paper = {
        "paper_id": "p1",
        "title": "Test Paper",
        "num_pages": 1,
        "pages": [{"index": 1, "text": "x"}],
        "references": [
            {
                "index": 1,
                "raw": (
                    "[1] Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. "
                    "Layer normalization. arXiv preprint arXiv:1607.06450, 2016."
                ),
                "title": "Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton",
                "arxiv_id": "1607.06450",
            }
        ],
        "meta": {},
    }
    (tmp_path / "p1.json").write_text(json.dumps(paper, ensure_ascii=False), encoding="utf-8")
    entries = [{"title": "Layer normalization", "arxiv_id": "1607.06450"}]
    chat = StubChat(reply=json.dumps(entries, ensure_ascii=False))
    client = make_client(chat, parsed_dir=tmp_path)
    resp = client.post("/api/writing/references", json={"paper_ids": ["p1"]})
    assert resp.status_code == 200
    refs = resp.json()["references"]
    assert len(refs) == 1
    assert refs[0]["source"].startswith("corpus:p1")
    assert refs[0]["title"] == "Layer normalization"


def test_missing_query_422():
    """topic 与 paper_ids 均缺失 → 422。"""
    client = make_client(StubChat(reply="[]"))
    resp = client.post("/api/writing/references", json={})
    assert resp.status_code == 422
