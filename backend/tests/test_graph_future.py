"""模块五测试：future_work.py —— 节提取、三态判定、时间线排序。"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.graph_models import CitationGraph, GraphNode
from app.services.future_work import (
    extract_future_work,
    extract_section_texts,
    keywords_of,
    split_items,
)


def make_contract(pid: str, title: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "paper_id": pid,
        "title": title,
        "num_pages": 1,
        "pages": [{"page_no": 1, "blocks": blocks}],
        "references": [],
        "meta": {"sha256": "0" * 64, "source_filename": f"{pid}.pdf"},
    }


P1 = make_contract(
    "f1", "Early Work",
    [
        {"type": "heading", "text": "1 Introduction", "level": 1},
        {"type": "text", "text": "Intro text about the method."},
        {"type": "heading", "text": "7 Future Work", "level": 1},
        {
            "type": "text",
            "text": "We leave multilingual retrieval augmentation for future exploration.",
        },
        {
            "type": "text",
            "text": (
                "Robustness against adversarial perturbation in tokenization "
                "remains difficult."
            ),
        },
        {"type": "text", "text": "Quantum entanglement syntax deserves investigation."},
    ],
)
P2 = make_contract(
    "f2", "Later Work",
    [
        {"type": "heading", "text": "3 Method", "level": 1},
        {
            "type": "text",
            "text": (
                "Our method enables multilingual retrieval augmentation with "
                "robustness and adversarial training."
            ),
        },
        {"type": "heading", "text": "5 Conclusion", "level": 1},
        {
            "type": "text",
            "text": "We summarize the contributions of this paper in one long sentence.",
        },
    ],
)
P3 = make_contract(
    "f3", "Middle Work",
    [
        {"type": "heading", "text": "2 Related Work", "level": 1},
        {"type": "text", "text": "Unrelated background about datasets and benchmarks."},
    ],
)

GRAPH = CitationGraph(
    graph_id="gtest00000003",
    created_at="2026-01-01T00:00:00+00:00",
    nodes=[
        GraphNode(paper_id="f1", title="Early Work", year=2018),
        GraphNode(paper_id="f3", title="Middle Work", year=2019),
        GraphNode(paper_id="f2", title="Later Work", year=2020),
    ],
    edges=[],
)
CONTRACTS = {"f1": P1, "f2": P2, "f3": P3}


def test_section_extraction():
    """只取 Future Work/Conclusion 节；Related Work 不取。"""
    sections = extract_section_texts(P1)
    assert len(sections) == 1
    assert "multilingual retrieval augmentation" in sections[0]
    assert "Intro text" not in sections[0]
    assert extract_section_texts(P3) == []


def test_split_and_keywords():
    """句子切分过滤短句；关键词去停用词。"""
    item = "We leave multilingual retrieval augmentation for future exploration."
    items = split_items(f"Short. {item}")
    assert items == [item]
    kws = keywords_of(
        "Robustness against adversarial perturbation in tokenization remains difficult."
    )
    assert set(kws) == {"robustness", "adversarial", "perturbation", "tokenization"}


def test_future_work_three_states():
    """solved / partially / unresolved 三态判定（关键词命中率阈值）。"""
    resp = extract_future_work(GRAPH, CONTRACTS)
    statuses = {}
    for it in resp.timeline:
        if it.paper_id != "f1":
            continue
        if it.item.startswith("We leave multilingual"):
            statuses["solved"] = it
        elif it.item.startswith("Robustness against"):
            statuses["partially"] = it
        elif it.item.startswith("Quantum entanglement"):
            statuses["unresolved"] = it
    assert statuses["solved"].status == "solved"
    assert statuses["partially"].status == "partially"
    assert statuses["unresolved"].status == "unresolved"
    assert "命中关键词 3/3" in statuses["solved"].evidence
    assert "2/4" in statuses["partially"].evidence
    assert statuses["unresolved"].status == "unresolved"
    assert "未解决" in statuses["unresolved"].evidence
    assert statuses["solved"].proposed_by == "Early Work"
    assert statuses["solved"].year == 2018


def test_future_work_timeline_sorted():
    """时间线按年份升序；无后续论文的条目判定 unresolved。"""
    resp = extract_future_work(GRAPH, CONTRACTS)
    years = [it.year if it.year is not None else 9999 for it in resp.timeline]
    assert years == sorted(years)
    assert resp.timeline[0].paper_id == "f1"
    later_items = [it for it in resp.timeline if it.paper_id == "f2"]
    assert later_items
    assert all(it.status == "unresolved" for it in later_items)
    assert any("无年份更晚" in it.evidence for it in later_items)
