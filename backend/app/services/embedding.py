"""嵌入后端：抽象接口 + OpenAI 兼容 /embeddings 适配 + 确定性 HashEmbedding。

配置（不改 config.py，直接读 env）：
- EMBEDDING_BASE_URL / EMBEDDING_MODEL：OpenAI 兼容嵌入服务；
- EMBEDDING_API_KEY 缺省回退 LLM_API_KEY；
- 未配置 EMBEDDING_MODEL 时默认使用 HashEmbedding（零网络，测试用）。
"""
from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod

import httpx

from app.core.config import settings


class EmbeddingError(RuntimeError):
    """嵌入服务调用失败。"""


class EmbeddingBackend(ABC):
    """嵌入抽象接口。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本 → 向量列表（与输入同序）。"""


class HashEmbedding(EmbeddingBackend):
    """字符 n-gram 哈希嵌入：确定性、零网络，用于测试与离线运行。"""

    def __init__(self, dim: int = 256, ngram_sizes: tuple[int, ...] = (2, 3)) -> None:
        if dim <= 0:
            raise ValueError("dim 必须为正整数")
        self.dim = dim
        self.ngram_sizes = ngram_sizes

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for n in self.ngram_sizes:
            if len(text) < n:
                continue
            for i in range(len(text) - n + 1):
                digest = hashlib.md5(text[i : i + n].encode("utf-8")).digest()
                h = int.from_bytes(digest[:8], "big")
                vec[h % self.dim] += 1.0 if h & 1 else -1.0
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class OpenAICompatibleEmbedding(EmbeddingBackend):
    """OpenAI 兼容 /embeddings 适配器。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("EMBEDDING_BASE_URL")
            or settings.effective_embedding_base_url
        ).rstrip("/")
        self.model = model or os.environ.get("EMBEDDING_MODEL") or settings.embedding_model
        if not self.model:
            raise EmbeddingError("EMBEDDING_MODEL 未配置，无法调用嵌入服务")
        self.api_key = api_key or os.environ.get("EMBEDDING_API_KEY") or settings.llm_api_key
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 16):
            batch = texts[start : start + 16]
            payload = {"model": self.model, "input": batch}
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        f"{self.base_url}/embeddings", headers=headers, json=payload
                    )
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda d: d.get("index", 0))
                vectors.extend([d["embedding"] for d in data])
            except httpx.TimeoutException as exc:
                raise EmbeddingError(f"嵌入服务超时（{self.timeout}s）: {self.base_url}") from exc
            except httpx.HTTPStatusError as exc:
                raise EmbeddingError(
                    f"嵌入服务返回错误 {exc.response.status_code}: {exc.response.text[:200]}"
                ) from exc
            except httpx.TransportError as exc:
                raise EmbeddingError(f"嵌入服务网络错误: {exc}") from exc
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise EmbeddingError(f"嵌入服务响应格式异常: {exc}") from exc
        return vectors


def get_embedding_backend() -> EmbeddingBackend:
    """配置了 EMBEDDING_MODEL 时用 OpenAI 兼容适配，否则用确定性 HashEmbedding。"""
    if os.environ.get("EMBEDDING_MODEL") or settings.embedding_model:
        return OpenAICompatibleEmbedding()
    return HashEmbedding()
