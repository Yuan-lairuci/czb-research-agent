"""模块五 API：/api/graph —— 建图、指标分析、图数据、综述、未来工作。

独立可测：create_graph_router(data_dir=...) 可注入数据目录；LLM 经
get_llm()/set_llm() 注入（仿 api/qa.py get_engine/set_engine 惯例），
缺省走 app.services.chat_service（LLM 一律经 chat_service，不直连网络）。
main.py 注册（由调度员执行）::

    from app.api.graph import router as graph_router
    app.include_router(graph_router)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models.graph_models import (
    AnalyzeRequest,
    CitationGraph,
    FutureWorkRequest,
    FutureWorkResponse,
    GraphAnalyzeResponse,
    GraphBuildRequest,
    GraphBuildResponse,
    ReviewRequest,
    ReviewResponse,
)
from app.services import future_work as future_work_service
from app.services import graph_build, graph_metrics, review_gen
from app.services.chat_service import chat_service
from app.services.graph_build import GraphBuildError, ParsedPaperNotFoundError

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

_llm: Any = chat_service


def get_llm() -> Any:
    """获取 LLM 客户端（缺省 chat_service 实例；None = 模板模式）。"""
    return _llm


def set_llm(llm: Any) -> None:
    """注入/清空 LLM 客户端（测试用）。"""
    global _llm
    _llm = llm


def create_graph_router(data_dir: Path | str | None = None) -> APIRouter:
    """构建图谱路由；data_dir 缺省为 backend/data/。"""
    data_root = Path(data_dir) if data_dir is not None else _DEFAULT_DATA_DIR
    parsed_dir = data_root / "parsed"
    graphs_dir = data_root / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)

    router = APIRouter(prefix="/api/graph", tags=["graph"])

    def _load_graph(graph_id: str) -> CitationGraph:
        try:
            return graph_build.load_graph(graphs_dir, graph_id)
        except GraphBuildError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _load_contracts(graph: CitationGraph) -> dict[str, dict[str, Any]]:
        try:
            return graph_build.load_corpus([n.paper_id for n in graph.nodes], parsed_dir)
        except ParsedPaperNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/build", response_model=GraphBuildResponse)
    def build(req: GraphBuildRequest) -> GraphBuildResponse:
        """建局域引用图（paper_ids 缺省 = 库内全部论文）。"""
        try:
            graph = graph_build.build_graph(
                req.paper_ids, parsed_dir=parsed_dir, graphs_dir=graphs_dir
            )
        except ParsedPaperNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return GraphBuildResponse(
            graph_id=graph.graph_id,
            num_nodes=len(graph.nodes),
            num_edges=len(graph.edges),
        )

    @router.post("/analyze", response_model=GraphAnalyzeResponse)
    async def analyze(req: AnalyzeRequest) -> GraphAnalyzeResponse:
        """PageRank / degree / betweenness + 分类 + 解读，并把指标写回图文件。"""
        graph = _load_graph(req.graph_id)
        result = await graph_metrics.analyze_graph(graph, llm=get_llm())
        graph.metrics = list(result.metrics)
        graph_build.save_graph(graphs_dir, graph)
        return result

    @router.post("/review", response_model=ReviewResponse)
    async def review(req: ReviewRequest) -> ReviewResponse:
        """生成学术综述（抗幻觉：越界 [n] 剔除并标注）。"""
        graph = _load_graph(req.graph_id)
        contracts = _load_contracts(graph)
        return await review_gen.generate_review(
            graph, contracts, topic=req.topic, llm=get_llm()
        )

    @router.post("/future-work", response_model=FutureWorkResponse)
    def future_work(req: FutureWorkRequest) -> FutureWorkResponse:
        """挖掘未竟方向，按年份排时间线并判定三态。"""
        graph = _load_graph(req.graph_id)
        contracts = _load_contracts(graph)
        return future_work_service.extract_future_work(graph, contracts)

    @router.get("/{graph_id}", response_model=CitationGraph)
    def get_graph(graph_id: str) -> CitationGraph:
        """返回 nodes/edges/metrics 供前端渲染。"""
        return _load_graph(graph_id)

    return router


router = create_graph_router()
