"""模块四测试：推荐排序（GitHub 加分 / 反馈加权）、by-paper 推荐流、抓取入库。"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.paper_models import PaperDB
from app.services.recommend import FeedbackStore, recommend_by_paper, score_entry

CORPUS = Path("D:/deep/czb_assets/testdata/1706.03762v1.pdf")

BASE_PAYLOAD = {
    "paperId": "s2-base",
    "title": "Baseline Paper",
    "abstract": "A baseline.",
    "year": 2020,
    "citationCount": 99,
    "influentialCitationCount": 0,
    "externalIds": {"ArXiv": "2001.00001"},
}


class StubS2:
    def __init__(self, refs=None, cits=None):
        self.refs = refs or []
        self.cits = cits or []

    def get_references(self, paper_id, limit=100):
        return self.refs

    def get_citations(self, paper_id, limit=100):
        return self.cits


class StubArxiv:
    def __init__(self, pdf: bytes, meta=None):
        self.pdf = pdf
        self.meta = meta or {"arxiv_id": "1706.03762v7", "title": "Attention Is All You Need"}

    def download_pdf(self, arxiv_id):
        return self.pdf

    def get_paper(self, arxiv_id):
        return self.meta


def _payload(**kw):
    p = dict(BASE_PAYLOAD)
    p.update(kw)
    return p


def test_score_github_bonus():
    """同参数下带 GitHub 仓库的论文多 GITHUB_BONUS 分。"""
    plain, _ = score_entry(_payload())
    with_gh, reasons = score_entry(
        _payload(externalIds={"Code": "https://github.com/foo/bar"})
    )
    assert round(with_gh - plain, 6) == 0.5
    assert any("GitHub" in r for r in reasons)


def test_score_feedback_weighting():
    """点赞 +1 分、点踩 -1 分（FEEDBACK_WEIGHT=1）。"""
    base, _ = score_entry(_payload())
    liked, reasons_like = score_entry(_payload(), feedback_delta=1)
    disliked, reasons_dislike = score_entry(_payload(), feedback_delta=-1)
    assert round(liked - base, 6) == 1.0
    assert round(disliked - base, 6) == -1.0
    assert any("点赞" in r for r in reasons_like)
    assert any("点踩" in r for r in reasons_dislike)


def test_score_recency_decay():
    """新近度：2024 年论文比 2010 年得分高，其它字段相同。"""
    new, reasons = score_entry(_payload(year=2024), now_year=2025)
    old, _ = score_entry(_payload(year=2010), now_year=2025)
    assert new > old
    assert any("2024" in r for r in reasons)


def _edge(title, *, intent, context, pid, year=2021, cites=50, github=None):
    ext = {"ArXiv": f"19{pid}.00001"}
    if github:
        ext["Code"] = github
    return {
        "paper": {
            "paperId": f"s2-{pid}",
            "title": title,
            "abstract": "abstract",
            "year": year,
            "citationCount": cites,
            "influentialCitationCount": 2,
            "externalIds": ext,
        },
        "contexts": [context],
        "intents": [intent],
    }


def test_by_paper_ranking_and_reasons(tmp_path):
    """by-paper：过滤含金量不足引用、排序、理由、点赞反馈进入打分。"""
    refs = [
        _edge("Perfunctory", intent="background", context="see also", pid="00"),
        _edge("Method Used", intent="methodology", context="we adopt the method of [3]", pid="01"),
        _edge("Has Code", intent="methodology", context="their codebase is reused [4]",
              pid="02", github="https://github.com/x/y"),
    ]
    cits = [
        _edge("Citing Work", intent="background",
              context="the benchmark in this work is widely used in our experiments", pid="03"),
    ]
    store = FeedbackStore(tmp_path / "feedback.db")
    store.record("s2-00", "2100.00001", like=True)  # 被过滤条目的点赞不应影响结果
    store.record("s2-02", None, like=True)  # Has Code 点赞 +1

    resp = recommend_by_paper(
        "2101.00001", top_k=3, s2=StubS2(refs, cits), store=store, now_year=2025
    )

    assert resp.dropped_perfunctory == 1
    assert resp.references_considered == 3
    assert resp.citations_considered == 1
    titles = [r.title for r in resp.recommendations]
    assert "Perfunctory" not in titles
    assert set(titles) == {"Method Used", "Has Code", "Citing Work"}
    # Has Code：GitHub 加分 + 点赞加权，应排第一
    assert titles[0] == "Has Code"
    for rec in resp.recommendations:
        assert rec.reasons, f"{rec.title} 缺少推荐理由"
        assert any("参考文献" in r or "引用" in r for r in rec.reasons)
    rels = {r.title: r.relation for r in resp.recommendations}
    assert rels["Method Used"] == "reference"
    assert rels["Citing Work"] == "citation"


def test_by_paper_top_k_limits(tmp_path):
    """top_k 截断排序后的结果。"""
    refs = [
        _edge(f"Paper {i}", intent="methodology", context="substantive context here for the work",
              pid=f"1{i}", cites=10 * (i + 1))
        for i in range(5)
    ]
    resp = recommend_by_paper("2101.00001", top_k=2, s2=StubS2(refs, []), store=None, now_year=2025)
    assert len(resp.recommendations) == 2
    scores = [r.score for r in resp.recommendations]
    assert scores == sorted(scores, reverse=True)


def test_by_paper_skips_seed_paper(tmp_path):
    """种子论文自身出现在引用列表时不应推荐给自己。"""
    seed = _edge("Seed", intent="methodology", context="self reference kept for test", pid="99")
    seed["paper"]["externalIds"]["ArXiv"] = "2101.00001"
    resp = recommend_by_paper(
        "2101.00001", top_k=5, s2=StubS2([seed], []), store=None, now_year=2025
    )
    assert resp.recommendations == []


def test_fetch_and_ingest_writes_library(tmp_path):
    """抓取 PDF 走模块一入库布局：library/{id}.pdf + parsed/{id}.json + library.db。"""
    from app.services.recommend import fetch_and_ingest

    payload = CORPUS.read_bytes()
    expected_id = hashlib.sha256(payload).hexdigest()[:12]
    resp = fetch_and_ingest("1706.03762v7", arxiv=StubArxiv(payload), data_dir=tmp_path)
    assert resp.parse_status == "parsed"
    assert resp.paper_id == expected_id
    assert resp.num_pages and resp.num_pages > 0
    assert (tmp_path / "library" / f"{expected_id}.pdf").exists()
    assert (tmp_path / "parsed" / f"{expected_id}.json").exists()
    row = PaperDB(tmp_path / "library" / "library.db").get(expected_id)
    assert row is not None and row["sha256"] == hashlib.sha256(payload).hexdigest()

    # 再次抓取同一 PDF → duplicate，不重复入库
    again = fetch_and_ingest("1706.03762v7", arxiv=StubArxiv(payload), data_dir=tmp_path)
    assert again.parse_status == "duplicate"
    assert again.paper_id == expected_id
