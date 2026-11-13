"""模块五数据模型：局域引用图谱、指标、综述与未来工作契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """图谱节点 = 语料内一篇论文。"""

    paper_id: str
    title: str
    year: int | None = None
    arxiv_id: str | None = None


class GraphEdge(BaseModel):
    """有向边：source 引用 target（仅语料内边）。"""

    source: str
    target: str
    ref_index: int = Field(description="citing 论文 references 中被引条目的 index")
    context: str = Field(default="", description="citing 正文 [n] 标记附近句子；找不到为空串")


class NodeMetrics(BaseModel):
    """单节点中心性指标 + 分类 + 自然语言解读。"""

    paper_id: str
    title: str
    pagerank: float
    degree_centrality: float
    betweenness_centrality: float
    category: Literal["基石论文", "边缘衍生论文"]
    interpretation: str = ""


class CitationGraph(BaseModel):
    """持久化图谱（backend/data/graphs/{graph_id}.json）。"""

    graph_id: str
    created_at: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    metrics: list[NodeMetrics] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API 请求 / 响应
# ---------------------------------------------------------------------------


class GraphBuildRequest(BaseModel):
    """POST /api/graph/build 请求。"""

    paper_ids: list[str] | None = None


class GraphBuildResponse(BaseModel):
    """建图结果摘要。"""

    graph_id: str
    num_nodes: int
    num_edges: int


class AnalyzeRequest(BaseModel):
    """POST /api/graph/analyze 请求。"""

    graph_id: str


class GraphAnalyzeResponse(BaseModel):
    """指标分析结果。"""

    graph_id: str
    metrics: list[NodeMetrics]
    overall_interpretation: str = ""


class CitationRef(BaseModel):
    """[n] 标记到语料论文的映射。"""

    n: int
    paper_id: str
    title: str


class ReviewRequest(BaseModel):
    """POST /api/graph/review 请求。"""

    graph_id: str
    topic: str | None = None


class ReviewSection(BaseModel):
    """综述小节。"""

    heading: str
    content: str


class ReviewResponse(BaseModel):
    """综述生成结果（含抗幻觉标注）。"""

    graph_id: str
    topic: str | None = None
    generated_by: Literal["llm", "template"] = "template"
    sections: list[ReviewSection]
    citation_map: list[CitationRef]
    dropped_citations: list[str] = Field(
        default_factory=list, description="越界 [n] 引用剔除标注"
    )


class FutureWorkRequest(BaseModel):
    """POST /api/graph/future-work 请求。"""

    graph_id: str


class FutureWorkItem(BaseModel):
    """一条未竟方向及其解决状态判定。"""

    item: str
    proposed_by: str = Field(description="提出论文标题")
    paper_id: str
    year: int | None = None
    status: Literal["solved", "unresolved", "partially"]
    evidence: str = ""


class FutureWorkResponse(BaseModel):
    """未来工作时间线（按年份升序）。"""

    graph_id: str
    timeline: list[FutureWorkItem]


def graph_node_dump(node: GraphNode) -> dict[str, Any]:
    """节点序列化辅助（前端渲染用）。"""
    return node.model_dump()
