"""模块二 API：/api/qa —— 建索引、状态、带溯源问答。

独立可测：测试用独立 FastAPI app 挂载本 router（不依赖 main.py 注册）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.qa_models import (
    AskRequest,
    AskResponse,
    IndexRequest,
    IndexResponse,
    StatusResponse,
)
from app.services.chat_service import ChatServiceError
from app.services.chunker import ChunkerContractError
from app.services.rag_qa import (
    ParsedPaperNotFoundError,
    RagError,
    RagQAEngine,
)

router = APIRouter(prefix="/api/qa", tags=["qa"])

_engine: RagQAEngine | None = None


def get_engine() -> RagQAEngine:
    """获取（或懒创建）模块级引擎实例。"""
    global _engine
    if _engine is None:
        _engine = RagQAEngine()
    return _engine


def set_engine(engine: RagQAEngine | None) -> None:
    """注入/清空引擎实例（测试用）。"""
    global _engine
    _engine = engine


@router.post("/index", response_model=IndexResponse)
def index_paper(req: IndexRequest) -> IndexResponse:
    """对 parsed/{paper_id}.json 切块、嵌入并入库。"""
    try:
        result = get_engine().index_paper(req.paper_id)
    except ParsedPaperNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChunkerContractError as exc:
        raise HTTPException(status_code=422, detail=f"解析文件不符合契约: {exc}") from exc
    return IndexResponse(
        paper_id=req.paper_id,
        num_chunks=result["num_chunks"],
        num_pages=result["num_pages"],
    )


@router.get("/status", response_model=StatusResponse)
def status() -> StatusResponse:
    """已索引论文列表与 chunk 数。"""
    info = get_engine().status()
    return StatusResponse(papers=info["papers"], total_chunks=info["total_chunks"])


@router.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    """带原文溯源的 RAG 问答（支持单篇与跨文对比）。"""
    try:
        return await get_engine().ask(
            question=req.question, paper_ids=req.paper_ids, top_k=req.top_k
        )
    except RagError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ChatServiceError as exc:
        raise HTTPException(status_code=502, detail=f"生成服务不可用: {exc}") from exc
