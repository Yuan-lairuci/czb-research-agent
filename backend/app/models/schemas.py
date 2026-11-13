"""API 数据模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str = Field(default="ok", description="服务状态")
    version: str = Field(default="0.1.0", description="服务版本")


class ChatMessage(BaseModel):
    """单条对话消息。"""

    role: str = Field(description="角色：system / user / assistant")
    content: str = Field(description="消息内容")


class ChatRequest(BaseModel):
    """对话请求（骨架，供后续功能模块使用）。"""

    messages: list[ChatMessage] = Field(description="对话消息列表")
    stream: bool = Field(default=False, description="是否流式返回")
