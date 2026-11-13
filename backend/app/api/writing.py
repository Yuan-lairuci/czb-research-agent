"""模块六 API：学术写作辅助与数据可视化 Copilot。

main.py 注册（两行，由调度员执行）：
    from app.api.writing import router as writing_router
    app.include_router(writing_router)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.writing_models import (
    ChartRequest,
    ChartResponse,
    DiagramRequest,
    DiagramResponse,
    OutlineRequest,
    OutlineResponse,
    ReferencesRequest,
    ReferencesResponse,
    Zh2EnRequest,
    Zh2EnResponse,
)
from app.services.chat_service import ChatServiceError
from app.services.translate_llm import TranslateEngine
from app.services.writing_chart import ChartEngine, ChartError
from app.services.writing_diagram import DiagramEngine, DiagramError
from app.services.writing_outline import OutlineEngine, OutlineError
from app.services.writing_references import ReferencesEngine, ReferencesError, SourcesUnavailableError

router = APIRouter(prefix="/api/writing", tags=["writing"])

_outline_engine: OutlineEngine | None = None
_references_engine: ReferencesEngine | None = None
_diagram_engine: DiagramEngine | None = None
_chart_engine: ChartEngine | None = None
_translator: TranslateEngine | None = None


def get_outline_engine() -> OutlineEngine:
    """懒创建大纲引擎。"""
    global _outline_engine
    if _outline_engine is None:
        _outline_engine = OutlineEngine()
    return _outline_engine


def set_outline_engine(engine: OutlineEngine | None) -> None:
    """注入/清空大纲引擎（测试用）。"""
    global _outline_engine
    _outline_engine = engine


def get_references_engine() -> ReferencesEngine:
    """懒创建参考文献引擎。"""
    global _references_engine
    if _references_engine is None:
        _references_engine = ReferencesEngine()
    return _references_engine


def set_references_engine(engine: ReferencesEngine | None) -> None:
    """注入/清空参考文献引擎（测试用）。"""
    global _references_engine
    _references_engine = engine


def get_diagram_engine() -> DiagramEngine:
    """懒创建示意图引擎。"""
    global _diagram_engine
    if _diagram_engine is None:
        _diagram_engine = DiagramEngine()
    return _diagram_engine


def set_diagram_engine(engine: DiagramEngine | None) -> None:
    """注入/清空示意图引擎（测试用）。"""
    global _diagram_engine
    _diagram_engine = engine


def get_chart_engine() -> ChartEngine:
    """懒创建图表引擎。"""
    global _chart_engine
    if _chart_engine is None:
        _chart_engine = ChartEngine()
    return _chart_engine


def set_chart_engine(engine: ChartEngine | None) -> None:
    """注入/清空图表引擎（测试用）。"""
    global _chart_engine
    _chart_engine = engine


def get_translator() -> TranslateEngine:
    """懒创建翻译引擎（复用 translate_llm）。"""
    global _translator
    if _translator is None:
        _translator = TranslateEngine()
    return _translator


def set_translator(engine: TranslateEngine | None) -> None:
    """注入/清空翻译引擎（测试用）。"""
    global _translator
    _translator = engine


@router.post("/outline", response_model=OutlineResponse)
async def outline(req: OutlineRequest) -> OutlineResponse:
    """LLM 生成论文框架（默认六节 + 要点）。"""
    if req.sections is not None and not any(s.strip() for s in req.sections):
        raise HTTPException(status_code=422, detail="sections 不能为空")
    try:
        return await get_outline_engine().build_outline(req.idea, req.sections)
    except OutlineError as exc:
        raise HTTPException(status_code=502, detail=f"生成服务不可用: {exc}") from exc


@router.post("/references", response_model=ReferencesResponse)
async def references(req: ReferencesRequest) -> ReferencesResponse:
    """生成经来源核对的真实参考文献（幻觉条目落 dropped）。"""
    try:
        return await get_references_engine().build_references(
            topic=req.topic,
            paper_ids=req.paper_ids,
            style=req.style,
            limit=req.limit,
        )
    except SourcesUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ReferencesError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/diagram", response_model=DiagramResponse)
async def diagram(req: DiagramRequest) -> DiagramResponse:
    """生成 TikZ/Graphviz/Mermaid 示意图脚本。"""
    try:
        return await get_diagram_engine().generate_diagram(req.kind, req.description)
    except DiagramError as exc:
        raise HTTPException(status_code=502, detail=f"生成服务不可用: {exc}") from exc


@router.post("/chart", response_model=ChartResponse)
async def chart(req: ChartRequest) -> ChartResponse:
    """构建 ECharts option 并生成英文 Figure Caption。"""
    try:
        return await get_chart_engine().build_chart(
            rows=req.rows,
            csv_text=req.csv_text,
            chart_type=req.chart_type,
            title=req.title,
        )
    except ChartError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/zh2en", response_model=Zh2EnResponse)
async def zh2en(req: Zh2EnRequest) -> Zh2EnResponse:
    """中文学术文本翻译为英文学术表达（复用 translate_llm）。"""
    try:
        translated, engine = await get_translator().translate_text(req.text, "zh2en")
    except ChatServiceError as exc:
        raise HTTPException(status_code=502, detail=f"生成服务不可用: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"翻译服务不可用: {exc}") from exc
    return Zh2EnResponse(translated=translated, engine=engine)
