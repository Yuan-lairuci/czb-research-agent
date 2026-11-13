"""引用图谱指标：networkx PageRank / degree / betweenness + 分类 + 自然语言解读。

分类规则：度为 0（孤立点）→ 边缘衍生论文；PageRank ≥ 均值 → 基石论文；
其余 → 边缘衍生论文。解读：每节点模板解读；LLM 可选（注入 llm.chat），
失败或未注入时给模板解读，零网络。
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Protocol

import networkx as nx

from app.models.graph_models import (
    CitationGraph,
    GraphAnalyzeResponse,
    NodeMetrics,
)

CATEGORY_FOUNDATION = "基石论文"
CATEGORY_PERIPHERAL = "边缘衍生论文"


class LLMClient(Protocol):
    """可注入的 LLM 接口（chat_service.ChatService 或测试 stub）。"""

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str: ...


class MetricsError(ValueError):
    """指标计算失败。"""


def _template_interpretation(node_title: str, m: NodeMetrics) -> str:
    """单节点模板解读。"""
    rank_desc = (
        "在语料引用网络中处于核心位置" if m.category == CATEGORY_FOUNDATION
        else "在语料引用网络中处于边缘位置"
    )
    return (
        f"《{node_title}》的 PageRank 为 {m.pagerank:.4f}、度中心性为 "
        f"{m.degree_centrality:.4f}、介数中心性为 {m.betweenness_centrality:.4f}，"
        f"{rank_desc}，被判定为{m.category}。"
    )


def compute_metrics(graph: CitationGraph) -> list[NodeMetrics]:
    """计算每节点 PageRank / degree / betweenness 并分类。"""
    if not graph.nodes:
        return []
    g = nx.DiGraph()
    for node in graph.nodes:
        g.add_node(node.paper_id)
    for edge in graph.edges:
        if edge.source in g and edge.target in g:
            g.add_edge(edge.source, edge.target)

    pagerank = nx.pagerank(g) if g.number_of_nodes() else {}
    degree = nx.degree_centrality(g)
    betweenness = nx.betweenness_centrality(g)
    pr_mean = mean(pagerank.values()) if pagerank else 0.0

    titles = {n.paper_id: n.title for n in graph.nodes}
    metrics: list[NodeMetrics] = []
    for node in graph.nodes:
        pid = node.paper_id
        isolated = g.degree(pid) == 0
        pr = float(pagerank.get(pid, 0.0))
        if not isolated and pr >= pr_mean:
            category = CATEGORY_FOUNDATION
        else:
            category = CATEGORY_PERIPHERAL
        m = NodeMetrics(
            paper_id=pid,
            title=titles.get(pid, pid),
            pagerank=pr,
            degree_centrality=float(degree.get(pid, 0.0)),
            betweenness_centrality=float(betweenness.get(pid, 0.0)),
            category=category,
        )
        m.interpretation = _template_interpretation(node.title, m)
        metrics.append(m)
    metrics.sort(key=lambda x: (-x.pagerank, x.paper_id))
    return metrics


def template_overall_interpretation(graph: CitationGraph, metrics: list[NodeMetrics]) -> str:
    """整体模板解读。"""
    if not metrics:
        return "语料为空，无可解读的引用网络。"
    top = metrics[0]
    num_found = sum(1 for m in metrics if m.category == CATEGORY_FOUNDATION)
    return (
        f"本引用图谱含 {len(graph.nodes)} 个节点、{len(graph.edges)} 条语料内引用边。"
        f"其中 {num_found} 篇被判定为基石论文，PageRank 最高的《{top.title}》"
        f"（{top.pagerank:.4f}）是该局域引用网络的枢纽；其余为边缘衍生论文。"
    )


async def overall_interpretation(
    graph: CitationGraph,
    metrics: list[NodeMetrics],
    llm: LLMClient | None = None,
) -> str:
    """整体解读：LLM 可选，失败回退模板（stub 时给模板解读）。"""
    if llm is None:
        return template_overall_interpretation(graph, metrics)
    prompt = (
        "以下是学术引用图谱的中心性分析结果，请用一两句中文概括该领域的研究结构"
        "（只依据给出的数据，不要编造）：\n"
        + "\n".join(
            f"- 《{m.title}》PageRank={m.pagerank:.4f} 分类={m.category}" for m in metrics
        )
    )
    try:
        text = await llm.chat(
            [{"role": "user", "content": prompt}], temperature=0.2
        )
    except Exception:  # noqa: BLE001 - LLM 可选，任何失败回退模板
        return template_overall_interpretation(graph, metrics)
    text = (text or "").strip()
    return text or template_overall_interpretation(graph, metrics)


async def analyze_graph(
    graph: CitationGraph, llm: LLMClient | None = None
) -> GraphAnalyzeResponse:
    """指标 + 分类 + 解读；不落盘（落盘由 API 层负责）。"""
    metrics = compute_metrics(graph)
    overall = await overall_interpretation(graph, metrics, llm)
    return GraphAnalyzeResponse(
        graph_id=graph.graph_id, metrics=metrics, overall_interpretation=overall
    )
