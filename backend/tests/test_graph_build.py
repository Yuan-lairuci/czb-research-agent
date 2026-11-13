"""模块五测试：graph_build.py —— 建图正确性（环/孤立点/匹配/上下文/持久化）。"""

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.services.graph_build import (
    ParsedPaperNotFoundError,
    build_graph,
    find_citation_context,
    load_graph,
    normalize_title,
)


def make_contract(
    paper_id: str,
    title: str,
    refs: list[dict[str, Any]] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    source_filename: str = "",
) -> dict[str, Any]:
    """最小合法契约 dict。"""
    return {
        "paper_id": paper_id,
        "title": title,
        "num_pages": 1,
        "pages": [{"page_no": 1, "blocks": blocks or []}],
        "references": refs or [],
        "meta": {"sha256": "0" * 64, "source_filename": source_filename},
    }


def write_parsed(parsed_dir: Path, contract: dict[str, Any]) -> Path:
    parsed_dir.mkdir(parents=True, exist_ok=True)
    path = parsed_dir / f"{contract['paper_id']}.json"
    path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    return path


def cycle_corpus(tmp_path: Path) -> Path:
    """A 引 B、B 引 C、C 引 A（环）+ D 孤立点。"""
    parsed_dir = tmp_path / "parsed"
    write_parsed(
        parsed_dir,
        make_contract(
            "a1", "Paper A",
            refs=[{"index": 1, "raw": "Paper B", "title": "Paper B", "arxiv_id": None}],
            blocks=[{"type": "text", "text": "We build on Paper B [1]."}],
        ),
    )
    write_parsed(
        parsed_dir,
        make_contract(
            "b1", "Paper B",
            refs=[{"index": 1, "raw": "Paper C", "title": "Paper C", "arxiv_id": None}],
            blocks=[{"type": "text", "text": "As Paper C [1] shows."}],
        ),
    )
    write_parsed(
        parsed_dir,
        make_contract(
            "c1", "Paper C",
            refs=[{"index": 1, "raw": "Paper A", "title": "Paper A", "arxiv_id": None}],
            blocks=[{"type": "text", "text": "Unlike Paper A [1], we focus on X."}],
        ),
    )
    write_parsed(parsed_dir, make_contract("d1", "Paper D"))
    return parsed_dir


def test_build_cycle_and_isolated(tmp_path):
    """环形引用 + 孤立点：节点全收，边仅语料内 3 条，孤立点无边。"""
    parsed_dir = cycle_corpus(tmp_path)
    graph = build_graph(
        None,
        parsed_dir=parsed_dir,
        graphs_dir=tmp_path / "graphs",
        meta_map={
            "a1": {"year": 2018}, "b1": {"year": 2019},
            "c1": {"year": 2020}, "d1": {"year": 2021},
        },
    )
    assert len(graph.nodes) == 4
    edge_pairs = {(e.source, e.target) for e in graph.edges}
    assert edge_pairs == {("a1", "b1"), ("b1", "c1"), ("c1", "a1")}
    degrees = {n.paper_id: 0 for n in graph.nodes}
    for e in graph.edges:
        degrees[e.source] += 1
        degrees[e.target] += 1
    assert degrees["d1"] == 0
    years = {n.paper_id: n.year for n in graph.nodes}
    assert years["a1"] == 2018 and years["c1"] == 2020


def test_edge_context_extraction(tmp_path):
    """[n] 标记句子进 context；找不到给空串；逗号多标记可命中。"""
    parsed_dir = tmp_path / "parsed"
    write_parsed(
        parsed_dir,
        make_contract(
            "x1", "Paper X",
            refs=[
                {"index": 2, "raw": "a", "title": "Paper Y", "arxiv_id": None},
                {"index": 5, "raw": "b", "title": "Paper Z", "arxiv_id": None},
                {"index": 7, "raw": "c", "title": "Paper W", "arxiv_id": None},
            ],
            blocks=[
                {
                    "type": "text",
                    "text": "First sentence. As shown in [2], the model works better. End.",
                },
                {"type": "text", "text": "Smith et al. [4, 5] proposed a trick."},
                {"type": "text", "text": "No marker at all here."},
            ],
        ),
    )
    write_parsed(parsed_dir, make_contract("y1", "Paper Y"))
    write_parsed(parsed_dir, make_contract("z1", "Paper Z"))
    write_parsed(parsed_dir, make_contract("w1", "Paper W"))
    graph = build_graph(None, parsed_dir=parsed_dir, graphs_dir=tmp_path / "graphs")
    ctx = {(e.target, e.context) for e in graph.edges}
    assert ("y1", "As shown in [2], the model works better.") in ctx
    assert ("z1", "Smith et al. [4, 5] proposed a trick.") in ctx
    assert ("w1", "") in ctx


def test_reference_matching_by_arxiv_and_title(tmp_path):
    """arxiv_id 优先匹配，标题归一兜底；非语料引用与自引忽略。"""
    parsed_dir = tmp_path / "parsed"
    write_parsed(
        parsed_dir,
        make_contract(
            "m1", "My Paper About Attention",
            refs=[
                {"index": 1, "raw": "x", "title": None, "arxiv_id": "1706.03762"},
                {
                    "index": 2,
                    "raw": "y",
                    "title": "BERT: Pre-training of Deep Bidirectional Transformers",
                    "arxiv_id": None,
                },
                {"index": 3, "raw": "z", "title": "Totally Unknown Paper Somewhere"},
                {"index": 4, "raw": "w", "title": "My Paper About Attention", "arxiv_id": None},
            ],
        ),
    )
    t1 = make_contract("t1", "Attention Is All You Need", source_filename="1706.03762v7.pdf")
    write_parsed(parsed_dir, t1)
    t2 = make_contract(
        "t2", "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
    )
    write_parsed(parsed_dir, t2)
    graph = build_graph(None, parsed_dir=parsed_dir, graphs_dir=tmp_path / "graphs")
    pairs = {(e.source, e.target) for e in graph.edges}
    assert pairs == {("m1", "t1"), ("m1", "t2")}
    nodes = {n.paper_id: n for n in graph.nodes}
    assert nodes["t1"].arxiv_id == "1706.03762"
    assert nodes["t1"].year == 2017


def test_graph_persist_load_and_deterministic_id(tmp_path):
    """图谱落盘/回读一致；同语料重复建图 graph_id 幂等。"""
    parsed_dir = cycle_corpus(tmp_path)
    graphs_dir = tmp_path / "graphs"
    g1 = build_graph(None, parsed_dir=parsed_dir, graphs_dir=graphs_dir)
    assert (graphs_dir / f"{g1.graph_id}.json").exists()
    loaded = load_graph(graphs_dir, g1.graph_id)
    assert [n.model_dump() for n in loaded.nodes] == [n.model_dump() for n in g1.nodes]
    assert [e.model_dump() for e in loaded.edges] == [e.model_dump() for e in g1.edges]
    g2 = build_graph(None, parsed_dir=parsed_dir, graphs_dir=graphs_dir)
    assert g2.graph_id == g1.graph_id


def test_build_subset_and_missing_id(tmp_path):
    """paper_ids 子集建图；未知 id 抛 ParsedPaperNotFoundError。"""
    parsed_dir = cycle_corpus(tmp_path)
    graphs_dir = tmp_path / "graphs"
    graph = build_graph(["a1"], parsed_dir=parsed_dir, graphs_dir=graphs_dir)
    assert [n.paper_id for n in graph.nodes] == ["a1"]
    assert graph.edges == []
    with pytest.raises(ParsedPaperNotFoundError):
        build_graph(["nope"], parsed_dir=parsed_dir, graphs_dir=graphs_dir)


def test_find_citation_context_direct():
    """直接测上下文抽取：区间展开 [1-3] 也命中。"""
    contract = make_contract(
        "q1", "Q",
        blocks=[{"type": "text", "text": "Prior work [1-3] established the base."}],
    )
    assert find_citation_context(contract, 3) == "Prior work [1-3] established the base."
    assert find_citation_context(contract, 9) == ""
    assert normalize_title("A B: C-D!") == "abcd"


def test_build_empty_corpus(tmp_path):
    """空语料 → 空图。"""
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir()
    graph = build_graph(None, parsed_dir=parsed_dir, graphs_dir=tmp_path / "graphs")
    assert graph.nodes == [] and graph.edges == []
