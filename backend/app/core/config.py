"""应用配置：全部从环境变量读取，严禁硬编码任何密钥。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    """读取环境变量，未设置时返回默认值。"""
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    """应用配置项（对应 .env.example 中的环境变量）。"""

    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://api.openai.com/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL"))
    embedding_base_url: str = field(default_factory=lambda: _env("EMBEDDING_BASE_URL"))

    @property
    def effective_embedding_base_url(self) -> str:
        """嵌入服务地址未配置时回退到 LLM 服务地址。"""
        return self.embedding_base_url or self.llm_base_url


settings = Settings()
