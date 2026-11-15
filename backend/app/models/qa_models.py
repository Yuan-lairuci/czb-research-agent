"""模块二 RAG 问答 API 数据模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatTurnIn(BaseModel):
    """一轮已完成的问答（多轮追问的记忆入参）。"""

    question: str = Field(min_length=1, description="用户问题")
    answer: str = Field(description="助手回答（不含引文块）")


class AskRequest(BaseModel):
    """问答请求。"""

    question: str = Field(min_length=1, description="用户问题")
    paper_ids: list[str] | None = Field(
        default=None, description="限定论文 id；为空则检索全部已索引论文"
    )
    top_k: int = Field(default=5, ge=1, le=20, description="召回块数")
    history: list[ChatTurnIn] = Field(
        default_factory=list, description="同会话此前问答历史（多轮追问用）"
    )


class SourceOut(BaseModel):
    """带原文溯源的来源条目（quote 逐字存在于 chunk 才 verified=True）。"""

    chunk_id: str
    paper_id: str
    page_no: int
    quote: str
    score: float
    verified: bool = Field(default=True, description="引文是否通过原文逐字校验")


class AskResponse(BaseModel):
    """问答响应。"""

    answer: str
    sources: list[SourceOut]
    verified: bool = Field(description="全部 [S#] 引文通过原文校验且可溯源")


class IndexRequest(BaseModel):
    """建索引请求。"""

    paper_id: str = Field(min_length=1, description="已解析论文 id（parsed/{id}.json）")


class IndexResponse(BaseModel):
    """建索引响应。"""

    paper_id: str
    num_chunks: int
    num_pages: int


class StatusResponse(BaseModel):
    """索引状态响应。"""

    papers: dict[str, int]
    total_chunks: int
