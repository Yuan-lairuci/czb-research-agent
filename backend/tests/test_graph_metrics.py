"""模块五测试：graph_metrics.py —— PageRank 基石判定、分类、解读回退。"""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.graph_models import CitationGraph, GraphEdge, GraphNode
from app.services.graph_metrics import (
    CATEGORY_FOUNDATION,
    CATEGORY_PERIPHERAL,
    analyze_graph,
    compute_metrics,
)


def make_graph(nodes: list[str], edges: list[tuple[str, str]]) -> CitationGraph:
    return CitationGraph(
        graph_id="gtest00000001",
        created_at="2026-01-01T00:00:00+00:00",
        nodes=[
            GraphNode(paper_id=p, title=f"Title of {p}", year=2018 + i)
            for i, p in enumerate(nodes)
        ],
        edges=[
            GraphEdge(source=s, target=t, ref_index=1, context="") for s, t in edges
        ],
    )


class ScriptedChat:
    """按脚本返回回答的 chat stub；记录收到的 messages。"""

    def __init__(self, answer: str, error: bool = False) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append(messages)
        if self.error:
            raise RuntimeError("llm down")
        return self.answer


def test_pagerank_foundational_top():
    """被多方引用的枢纽 = PageRank top = 基石论文；引用方为边缘衍生。"""
    graph = make_graph(["hub", "c1", "c2", "c3"], [("c1", "hub"), ("c2", "hub"), ("c3", "hub")])
    metrics = compute_metrics(graph)
    by_id = {m.paper_id: m for m in metrics}
    assert metrics[0].paper_id == "hub"
    assert by_id["hub"].pagerank > by_id["c1"].pagerank
    assert by_id["hub"].category == CATEGORY_FOUNDATION
    for pid in ("c1", "c2", "c3"):
        assert by_id[pid].category == CATEGORY_PERIPHERAL


def test_cycle_vs_isolated_classification():
    """环内节点互引 → 基石；孤立点 → 边缘衍生。"""
    graph = make_graph(
        ["a", "b", "c", "lonely"],
        [("a", "b"), ("b", "c"), ("c", "a")],
    )
    by_id = {m.paper_id: m for m in compute_metrics(graph)}
    for pid in ("a", "b", "c"):
        assert by_id[pid].category == CATEGORY_FOUNDATION
    assert by_id["lonely"].category == CATEGORY_PERIPHERAL
    assert by_id["a"].pagerank == pytest.approx(by_id["b"].pagerank)


def test_interpretation_template_and_llm_paths():
    """模板解读恒有；LLM stub 命中；LLM 失败回退模板（stub 时给模板解读）。"""
    graph = make_graph(["hub", "c1"], [("c1", "hub")])

    import asyncio

    result = asyncio.run(analyze_graph(graph, llm=None))
    assert "PageRank" in result.metrics[0].interpretation
    assert "基石论文" in result.overall_interpretation or "枢纽" in result.overall_interpretation

    stub = ScriptedChat("LLM 概括：该网络呈单枢纽结构。")
    result2 = asyncio.run(analyze_graph(graph, llm=stub))
    assert result2.overall_interpretation == "LLM 概括：该网络呈单枢纽结构。"
    assert stub.calls

    failing = ScriptedChat("", error=True)
    result3 = asyncio.run(analyze_graph(graph, llm=failing))
    assert "枢纽" in result3.overall_interpretation


def test_metrics_empty_graph():
    """空图 → 空指标。"""
    graph = make_graph([], [])
    assert compute_metrics(graph) == []
