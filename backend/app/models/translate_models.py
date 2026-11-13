"""模块三翻译 API 数据模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 翻译方向：zh2en 中译英 / en2zh 英译中。
Direction = Literal["zh2en", "en2zh"]


class TranslateRequest(BaseModel):
    """文本翻译请求（JSON 非流式与 SSE 流式同参；stream=true 走 SSE）。"""

    text: str = Field(min_length=1, description="待翻译文本（Markdown 片段亦可）")
    direction: Direction = Field(default="en2zh", description="翻译方向")
    stream: bool = Field(default=False, description="true 时以 text/event-stream 流式返回")


class TranslateResponse(BaseModel):
    """文本翻译响应。"""

    translated: str = Field(description="译文（占位符已无损还原）")
    engine: str = Field(description="引擎标识：LLM 模型名；缓存命中为 cache")


class PageTranslateRequest(BaseModel):
    """页翻译请求：按解析契约逐块对齐翻译。"""

    paper_id: str = Field(
        min_length=1, pattern=r"^[\w-]+$", description="已解析论文 id（防路径穿越）"
    )
    page_no: int = Field(ge=1, description="页码（1 起）")
    direction: Direction = Field(default="en2zh", description="翻译方向")


class BlockTranslation(BaseModel):
    """页内一个块的对齐翻译结果。"""

    block_index: int = Field(description="块在页 blocks 数组中的下标（0 起）")
    src_text: str = Field(description="原文块")
    dst_text: str = Field(description="译文块；空文本块为 ''")


class PageResult(BaseModel):
    """整篇翻译中的一页。"""

    page_no: int
    blocks: list[BlockTranslation]


class PaperTranslateResponse(BaseModel):
    """整篇翻译进度/结果（增量缓存，已译页不再调 LLM）。"""

    paper_id: str
    direction: Direction
    total_pages: int = Field(description="论文总页数")
    translated_pages: int = Field(description="已完整翻译页数")
    done: bool = Field(description="translated_pages == total_pages")
    pages: list[PageResult] = Field(description="已译页结果（按页码升序）")
