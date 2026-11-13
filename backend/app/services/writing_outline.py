"""论文框架生成：LLM 输出严格 JSON，代码侧校验结构完整性。

契约：LLM 输出 {"sections": [{"title": str, "points": [str, ...]}, ...]}（允许
外裹 ```json 代码块）；每个期望小节必须出现（标题大小写不敏感），否则抛
OutlineError。LLM 调用失败（ChatServiceError）原样上抛，由 API 层映射 502。
测试注入 FakeChat 桩，零网络。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.models.writing_models import OutlineResponse, OutlineSection
from app.services.chat_service import ChatService, chat_service

DEFAULT_SECTIONS = [
    "Abstract",
    "Introduction",
    "Method",
    "Related Work",
    "Experiments",
    "Conclusion",
]

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class OutlineError(RuntimeError):
    """LLM 输出不符合论文框架契约（缺节 / 结构非法 / 非 JSON）。"""


def _extract_json(text: str) -> Any:
    """剥掉代码围栏后解析 JSON；失败抛 OutlineError。"""
    m = _FENCE_RE.search(text)
    payload = m.group(1).strip() if m else text.strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OutlineError(f"LLM 输出不是合法 JSON: {exc}") from exc


def _normalize_sections(parsed: Any) -> dict[str, OutlineSection]:
    """LLM 输出 -> {小写标题: OutlineSection}；结构非法抛 OutlineError。"""
    if isinstance(parsed, dict):
        items = parsed.get("sections")
    else:
        items = parsed
    if not isinstance(items, list):
        raise OutlineError("LLM 输出缺少 sections 列表")
    out: dict[str, OutlineSection] = {}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise OutlineError(f"sections[{i}] 不是对象")
        title = item.get("title")
        points = item.get("points")
        if not isinstance(title, str) or not title.strip():
            raise OutlineError(f"sections[{i}].title 非法")
        if not isinstance(points, list) or not all(isinstance(p, str) for p in points):
            raise OutlineError(f"sections[{i}].points 必须是字符串列表")
        out[title.strip().lower()] = OutlineSection(title=title.strip(), points=points)
    return out


class OutlineEngine:
    """注入式论文框架引擎（chat 可注入，独立可测零网络）。"""

    def __init__(self, chat: ChatService | None = None) -> None:
        self._chat = chat if chat is not None else chat_service

    @staticmethod
    def _build_messages(idea: str, sections: list[str]) -> list[dict[str, str]]:
        """构造 LLM messages：只输出 JSON，不输出任何解释。"""
        system = (
            "你是学术论文写作助手。根据用户的研究 idea 生成论文框架，"
            f"必须恰好包含这些小节：{', '.join(sections)}。"
            "每节给出 3-6 条要点。只输出一个 JSON 对象，格式为 "
            '{"sections": [{"title": "...", "points": ["...", ...]}, ...]}，'
            "不要输出任何解释、前后缀或 Markdown 围栏。"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": f"研究 idea：{idea}"},
        ]

    async def build_outline(
        self, idea: str, sections: list[str] | None = None
    ) -> OutlineResponse:
        """生成论文框架；返回结构保证与请求小节一一对应（顺序一致）。"""
        expected = [s.strip() for s in sections] if sections else list(DEFAULT_SECTIONS)
        expected = [s for s in expected if s]
        if not expected:
            raise OutlineError("sections 不能为空")
        raw = await self._chat.chat(self._build_messages(idea, expected))
        by_title = _normalize_sections(_extract_json(raw))
        out: list[OutlineSection] = []
        for title in expected:
            sec = by_title.get(title.lower())
            if sec is None:
                raise OutlineError(f"LLM 输出缺少小节: {title}")
            out.append(OutlineSection(title=title, points=sec.points))
        return OutlineResponse(idea=idea, sections=out)
