"""OpenAI 兼容 /chat/completions 流式请求封装（骨架）。

本任务只提供封装骨架，不接入真实密钥调用；
后续功能模块在此基础上扩展（多轮对话、工具调用、上下文裁剪等）。
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings


class ChatService:
    """面向 OpenAI 兼容 HTTP 接口的对话服务封装。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = settings.llm_api_key if api_key is None else api_key
        self.model = model or settings.llm_model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        return {"model": self.model, "messages": messages, **kwargs}

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """非流式对话，返回完整文本。"""
        payload = self._payload(messages, stream=False, **kwargs)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions", headers=self._headers(), json=payload
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def chat_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        """流式对话：解析 SSE data: 帧，逐段产出增量文本。"""
        payload = self._payload(messages, stream=True, **kwargs)
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[len("data:"):].strip()
                    if chunk == "[DONE]":
                        break
                    delta = json.loads(chunk)["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content


chat_service = ChatService()
