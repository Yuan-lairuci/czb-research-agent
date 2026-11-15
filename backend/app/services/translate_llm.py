"""翻译引擎：复用 chat_service 的 LLM 通道，负责掩码翻译、缓存与逐页增量进度。

提示词见 build_messages()；代码/公式/URL/数字保护见 translate_mask 模块。
解析契约见模块一 backend/data/parsed/{paper_id}.json。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

from app.models.translate_models import Direction
from app.services.chat_service import ChatService, chat_service
from app.services.translate_mask import StreamUnmasker, mask, unmask
from app.services.translate_store import TranslateStore


class ParsedPaperNotFoundError(FileNotFoundError):
    """未找到已解析论文（沿用模块一同名约定，独立定义避免引入向量依赖）。"""


class PageNotFoundError(FileNotFoundError):
    """论文存在但页码越界。"""


DIRECTION_INSTRUCTION: dict[str, str] = {
    "zh2en": "将下面的中文翻译成英文。",
    "en2zh": "将下面的英文翻译成中文。",
}

# 纯数字/符号块（页码、编号等）：无字母无汉字，译了也是胡话，直接原样返回
_TRIVIAL_RE = re.compile(r"^[^A-Za-z\u4e00-\u9fff]+$")
_PLACEHOLDER_RE = re.compile(r"\{\{\w+\}\}")
# LLM 元抱怨（答非所问）识别：命中则视同失败回退原文，绝不写缓存
_META_MARKERS = ("请提供需要翻译", "我会按你的要求")


def _needs_llm(src: str) -> bool:
    """块是否值得送 LLM：纯数字符号、单 token 碎片（<pad>/missing 等解析垃圾）、
    掩码后无可译文字的公式块一律跳过，原样返回。"""
    s = src.strip()
    if not s or _TRIVIAL_RE.match(s):
        return False
    if " " not in s and "\n" not in s and not re.search(r"[\u4e00-\u9fff]", s):
        return False
    masked, _ = mask(s)
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", _PLACEHOLDER_RE.sub("", masked)))


def default_parsed_dir() -> Path:
    """解析产物目录：TRANSLATE_PARSED_DIR 环境变量或 backend/data/parsed。"""
    env = os.environ.get("TRANSLATE_PARSED_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / "parsed"


def build_messages(masked_text: str, direction: Direction) -> list[dict[str, str]]:
    """构造 LLM messages：system 为翻译提示词，user 为掩码后的待译文本。

    提示词约定：只输出译文本身；{{M<n>}} 占位符原样保留、不翻译不删改；
    保持段落结构；术语采用学术惯用译法。把掩码文本放在 user 消息便于桩回显测试。
    """
    system = (
        "你是学术论文翻译引擎。"
        + DIRECTION_INSTRUCTION[direction]
        + "只输出译文本身，不要任何解释、前后缀或引号。"
        + "原文中的 {{M<n>}} 形式占位符必须原样保留，不得翻译、删改或拆分。"
        + "保持段落与换行结构，术语采用学术惯用译法。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": masked_text},
    ]


class TranslateEngine:
    """注入式翻译引擎（chat/store/parsed_dir 均可注入，独立可测零网络）。"""

    def __init__(
        self,
        chat: ChatService | None = None,
        store: TranslateStore | None = None,
        parsed_dir: str | Path | None = None,
    ) -> None:
        self._chat = chat if chat is not None else chat_service
        self._store = store if store is not None else TranslateStore()
        self._parsed_dir = (
            Path(parsed_dir) if parsed_dir is not None else default_parsed_dir()
        )

    @property
    def store(self) -> TranslateStore:
        """底层缓存存储（API 层可用于探测缓存命中以标注 engine 字段）。"""
        return self._store

    @property
    def model_name(self) -> str:
        """模型名（缓存 key 与 engine 字段共用；未配置时退化为 llm）。"""
        return getattr(self._chat, "model", "") or "llm"

    async def translate_text(self, text: str, direction: Direction) -> tuple[str, str]:
        """整段翻译，返回 (译文, engine 标签)。缓存命中返回 ("cache") 且不触达 LLM。"""
        cached = self._store.get_text(self.model_name, direction, text)
        if cached is not None:
            return cached, "cache"
        masked, mapping = mask(text)
        raw = await self._chat.chat(build_messages(masked, direction))
        translated = unmask(raw, mapping)
        if any(m in translated for m in _META_MARKERS):
            return text, "fallback"
        self._store.put_text(self.model_name, direction, text, translated)
        return translated, self.model_name

    def stream_translation(
        self, text: str, direction: Direction
    ) -> tuple[AsyncIterator[str], str]:
        """返回 (增量迭代器, engine 标签)。缓存命中一次性产出整段，不触达 LLM。"""
        cached = self._store.get_text(self.model_name, direction, text)
        if cached is not None:

            async def once() -> AsyncIterator[str]:
                yield cached

            return once(), "cache"
        return self._stream_llm(text, direction), self.model_name

    async def _stream_llm(self, text: str, direction: Direction) -> AsyncIterator[str]:
        """LLM 流式翻译：逐段 feed StreamUnmasker，结束后 flush 并写缓存。"""
        masked, mapping = mask(text)
        unmasker = StreamUnmasker(mapping)
        parts: list[str] = []
        async for chunk in self._chat.chat_stream(build_messages(masked, direction)):
            piece = unmasker.feed(chunk)
            if piece:
                parts.append(piece)
                yield piece
        tail = unmasker.flush()
        if tail:
            parts.append(tail)
            yield tail
        self._store.put_text(self.model_name, direction, text, "".join(parts))

    def _load_paper(self, paper_id: str) -> dict:
        path = self._parsed_dir / f"{paper_id}.json"
        if not path.is_file():
            raise ParsedPaperNotFoundError(f"未找到已解析论文: {path}")
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def _get_page(self, paper: dict, page_no: int) -> dict:
        pages = paper.get("pages", [])
        for page in pages:
            if page.get("page_no") == page_no:
                return page
        raise PageNotFoundError(f"页码越界: {page_no}（共 {len(pages)} 页）")

    async def translate_page(
        self, paper_id: str, page_no: int, direction: Direction
    ) -> list[dict]:
        """按解析契约逐块对齐翻译一页；空文本块与纯数字/符号块不触达 LLM，页内块并发翻译。

        返回 [{block_index, src_text, dst_text}]，同时写 page_cache 进度。
        """
        paper = self._load_paper(paper_id)
        page = self._get_page(paper, page_no)

        async def _one(index: int, src: str) -> dict:
            if not src.strip():
                dst = ""
            elif not _needs_llm(src):
                dst = src.strip()
            else:
                dst = (await self.translate_text(src, direction))[0]
            self._store.save_page_block(paper_id, direction, page_no, index, src, dst)
            return {"block_index": index, "src_text": src, "dst_text": dst}

        blocks = page.get("blocks", [])
        results = await asyncio.gather(
            *(_one(i, b.get("text", "") or "") for i, b in enumerate(blocks))
        )
        return list(results)

    async def translate_paper(
        self, paper_id: str, direction: Direction, fill: bool = True
    ) -> dict:
        """整篇翻译进度/结果：已译页不重复调 LLM；fill=False 只查进度不补译。

        返回 PaperTranslateResponse 契约 dict。
        """
        paper = self._load_paper(paper_id)
        pages = paper.get("pages", [])
        total = int(paper.get("num_pages") or len(pages))
        by_page: dict[int, dict[int, tuple[str, str]]] = {}
        for page_no, block_index, src, dst in self._store.get_paper_blocks(
            paper_id, direction
        ):
            by_page.setdefault(page_no, {})[block_index] = (src, dst)
        if fill:
            for page in pages:
                page_no = page.get("page_no")
                if page_no not in by_page:
                    blocks = await self.translate_page(paper_id, page_no, direction)
                    by_page[page_no] = {
                        b["block_index"]: (b["src_text"], b["dst_text"]) for b in blocks
                    }
        result_pages = [
            {
                "page_no": page_no,
                "blocks": [
                    {"block_index": idx, "src_text": src, "dst_text": dst}
                    for idx, (src, dst) in sorted(by_page[page_no].items())
                ],
            }
            for page_no in sorted(by_page)
        ]
        translated_pages = len(by_page)
        return {
            "paper_id": paper_id,
            "direction": direction,
            "total_pages": total,
            "translated_pages": translated_pages,
            "done": translated_pages >= total,
            "pages": result_pages,
        }
