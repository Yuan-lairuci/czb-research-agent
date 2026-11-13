"""翻译 API 路由：文本翻译（JSON / SSE）、页逐块翻译、整篇进度。

独立可测：测试用独立 FastAPI app 挂载本 router（不依赖 main.py 注册）。
注册方式（main.py）：from app.api.translate import router as translate_router
                     app.include_router(translate_router)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.models.translate_models import (
    Direction,
    PageTranslateRequest,
    TranslateRequest,
    TranslateResponse,
)
from app.services.chat_service import ChatServiceError
from app.services.translate_llm import (
    PageNotFoundError,
    ParsedPaperNotFoundError,
    TranslateEngine,
)

router = APIRouter(prefix="/api/translate", tags=["translate"])

_engine: TranslateEngine | None = None


def get_engine() -> TranslateEngine:
    """懒创建全局引擎（生产用默认 chat_service/TranslateStore）。"""
    global _engine
    if _engine is None:
        _engine = TranslateEngine()
    return _engine


def set_engine(engine: TranslateEngine | None) -> None:
    """测试注入或重置引擎。"""
    global _engine
    _engine = engine


class BlockTranslation(BaseModel):
    """页内一个块的对齐翻译（与解析契约 blocks 顺序一一对应）。"""

    block_index: int = Field(ge=0, description="页内块序号（0 起）")
    src_text: str
    dst_text: str


class PageResult(BaseModel):
    """一页的逐块翻译结果。"""

    page_no: int
    blocks: list[BlockTranslation]


class PaperTranslateResponse(BaseModel):
    """整篇翻译进度/结果：已译页不重复调 LLM。"""

    paper_id: str
    direction: Direction
    total_pages: int
    translated_pages: int
    done: bool
    pages: list[PageResult]


def _sse_frame(payload: dict) -> str:
    """SSE 帧：data: {json}\\n\\n（ensure_ascii=False 便于中文阅读）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _sse_events(text: str, direction: Direction) -> AsyncIterator[str]:
    """SSE 事件流：增量 data: {"delta":...,"done":false} 帧，收尾给出全量译文
    与 engine 标签，最后以 data: [DONE] 终止。

    客户端断开时生成器被 close，未完成的生成自然中止（优雅结束，不落半截缓存）。
    """
    parts: list[str] = []
    try:
        stream, engine_label = get_engine().stream_translation(text, direction)
        async for delta in stream:
            parts.append(delta)
            yield _sse_frame({"delta": delta, "done": False})
    except ChatServiceError as exc:
        yield _sse_frame({"error": f"生成服务不可用: {exc}", "done": False})
        yield "data: [DONE]\n\n"
        return
    full = "".join(parts)
    yield _sse_frame({"delta": "", "done": True, "translated": full, "engine": engine_label})
    yield "data: [DONE]\n\n"


@router.post("/text", response_model=TranslateResponse)
async def translate_text(req: TranslateRequest):
    """整段文本翻译：stream=false 返回 JSON {translated, engine}；
    stream=true 返回 text/event-stream 增量帧（SSE）。"""
    if req.stream:
        return StreamingResponse(
            _sse_events(req.text, req.direction),
            media_type="text/event-stream",
        )
    try:
        translated, engine_label = await get_engine().translate_text(req.text, req.direction)
        return TranslateResponse(translated=translated, engine=engine_label)
    except ChatServiceError as exc:
        raise HTTPException(status_code=502, detail=f"生成服务不可用: {exc}") from exc


@router.post("/page", response_model=list[BlockTranslation])
async def translate_page(req: PageTranslateRequest):
    """按解析契约逐块对齐翻译一页，返回 [{block_index, src_text, dst_text}]。"""
    try:
        blocks = await get_engine().translate_page(req.paper_id, req.page_no, req.direction)
        return [BlockTranslation(**b) for b in blocks]
    except (ParsedPaperNotFoundError, PageNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChatServiceError as exc:
        raise HTTPException(status_code=502, detail=f"生成服务不可用: {exc}") from exc


@router.get("/paper/{paper_id}", response_model=PaperTranslateResponse)
async def translate_paper(
    paper_id: Annotated[str, Path(pattern=r"^[\w-]+$")],
    direction: Direction = "en2zh",
    fill: bool = True,
):
    """整篇翻译进度/结果：fill=true 补译缺页，fill=false 只查进度不触达 LLM。"""
    try:
        result = await get_engine().translate_paper(paper_id, direction, fill=fill)
        return PaperTranslateResponse(**result)
    except (ParsedPaperNotFoundError, PageNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChatServiceError as exc:
        raise HTTPException(status_code=502, detail=f"生成服务不可用: {exc}") from exc


__all__ = [
    "BlockTranslation",
    "PageResult",
    "PaperTranslateResponse",
    "get_engine",
    "router",
    "set_engine",
]
