"""OpenAI 兼容 /chat/completions 流式请求封装（Wave-A 骨架 + 模块二扩展）。

扩展内容：明确的异常类型（超时/网络/HTTP 错误）与可配置超时；
不接入真实密钥调用；测试一律 mock，零网络。
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings


class ChatServiceError(RuntimeError):
    """对话服务调用失败的基类异常。"""


class ChatTimeoutError(ChatServiceError):
    """请求超时。"""


class ChatNetworkError(ChatServiceError):
    """网络传输错误。"""


class ChatAPIError(ChatServiceError):
    """HTTP 非 2xx 或响应格式异常。"""


class ChatService:
    """面向 OpenAI 兼容 HTTP 接口的对话服务封装。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = settings.llm_api_key if api_key is None else api_key
        self.model = model or settings.llm_model
        self.timeout = timeout

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
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions", headers=self._headers(), json=payload
                )
                resp.raise_for_status()
                data = resp.json()
            return data["choices"][0]["message"]["content"]
        except httpx.TimeoutException as exc:
            raise ChatTimeoutError(f"对话服务超时（{self.timeout}s）: {self.base_url}") from exc
        except httpx.HTTPStatusError as exc:
            raise ChatAPIError(
                f"对话服务返回错误 {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.TransportError as exc:
            raise ChatNetworkError(f"对话服务网络错误: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ChatAPIError(f"对话服务响应格式异常: {exc}") from exc

    async def chat_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        """流式对话：解析 SSE data: 帧，逐段产出增量文本。"""
        payload = self._payload(messages, stream=True, **kwargs)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
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
        except httpx.TimeoutException as exc:
            raise ChatTimeoutError(f"对话服务超时（{self.timeout}s）: {self.base_url}") from exc
        except httpx.HTTPStatusError as exc:
            raise ChatAPIError(
                f"对话服务返回错误 {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.TransportError as exc:
            raise ChatNetworkError(f"对话服务网络错误: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ChatAPIError(f"对话服务响应格式异常: {exc}") from exc


chat_service = ChatService()
