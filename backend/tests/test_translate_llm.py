"""TranslateEngine / TranslateStore 单元测试：零网络（FakeChat 桩）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.translate_llm import (
    PageNotFoundError,
    ParsedPaperNotFoundError,
    TranslateEngine,
)
from app.services.translate_store import TranslateStore


class FakeChat:
    """零网络 LLM 桩：chat 回显 user 内容加前缀，chat_stream 按 chunk_size 切块产出。"""

    def __init__(self, chunk_size: int = 3) -> None:
        self.model = "fake-model"
        self.chunk_size = chunk_size
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]], **kwargs: object) -> str:
        self.calls.append(messages)
        return "译文:" + messages[-1]["content"]

    async def chat_stream(self, messages, **kwargs: object):
        self.calls.append(messages)
        full = "译文:" + messages[-1]["content"]
        for i in range(0, len(full), self.chunk_size):
            yield full[i : i + self.chunk_size]


PROTECTED = "公式 $E=mc^2$、代码 `x=1`、距离 3.5km。"


def make_engine(tmp_path: Path, chunk_size: int = 3) -> tuple[TranslateEngine, FakeChat]:
    chat = FakeChat(chunk_size=chunk_size)
    store = TranslateStore(db_path=tmp_path / "cache.db")
    return TranslateEngine(chat=chat, store=store, parsed_dir=tmp_path), chat


def write_paper(tmp_path: Path, paper_id: str, pages: list[dict]) -> None:
    payload = {
        "paper_id": paper_id,
        "title": "测试论文",
        "num_pages": len(pages),
        "pages": pages,
        "references": [],
        "meta": {"sha256": "x", "source_filename": "t.pdf"},
    }
    (tmp_path / f"{paper_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_translate_text_caches_and_skips_llm(tmp_path: Path) -> None:
    """首次翻译调用 LLM；同 key 再译命中缓存不再调用，engine 标注为 cache。"""
    engine, chat = make_engine(tmp_path)
    text = "深度学习"

    t1, e1 = asyncio.run(engine.translate_text(text, "zh2en"))
    t2, e2 = asyncio.run(engine.translate_text(text, "zh2en"))

    assert t1 == "译文:深度学习"
    assert t2 == t1
    assert e1 == "fake-model"
    assert e2 == "cache"
    assert len(chat.calls) == 1


def test_mask_protection_survives_translation(tmp_path: Path) -> None:
    """回显桩下代码/公式/数字在译文中无损保留（mask→翻译→unmask 闭环）。"""
    engine, _ = make_engine(tmp_path)
    translated, _ = asyncio.run(engine.translate_text(PROTECTED, "zh2en"))
    assert translated == "译文:" + PROTECTED


def test_stream_translation_joins_to_full_and_caches(tmp_path: Path) -> None:
    """流式增量拼接结果与整段一致，占位符跨块也能还原，流结束写缓存。"""
    engine, chat = make_engine(tmp_path, chunk_size=3)
    chunks = asyncio.run(_collect(engine.stream_translation(PROTECTED, "zh2en")[0]))
    joined = "".join(chunks)
    assert joined == "译文:" + PROTECTED
    assert len(chat.calls) == 1
    assert engine.store.get_text("fake-model", "zh2en", PROTECTED) == joined


def test_stream_cache_hit_single_delta(tmp_path: Path) -> None:
    """缓存命中时流式一次性产出整段，不再触达 LLM。"""
    engine, chat = make_engine(tmp_path)
    asyncio.run(engine.translate_text(PROTECTED, "zh2en"))
    calls_before = len(chat.calls)

    it, label = engine.stream_translation(PROTECTED, "zh2en")
    chunks = asyncio.run(_collect(it))

    assert label == "cache"
    assert chunks == ["译文:" + PROTECTED]
    assert len(chat.calls) == calls_before


def test_translate_page_block_alignment(tmp_path: Path) -> None:
    """页翻译按解析契约逐块对齐；空文本块不触达 LLM。"""
    write_paper(
        tmp_path,
        "p1",
        [
            {
                "page_no": 1,
                "blocks": [
                    {"type": "text", "text": "你好"},
                    {"type": "text", "text": "  "},
                ],
            }
        ],
    )
    engine, chat = make_engine(tmp_path)
    blocks = asyncio.run(engine.translate_page("p1", 1, "zh2en"))
    assert [b["block_index"] for b in blocks] == [0, 1]
    assert blocks[0]["src_text"] == "你好"
    assert blocks[0]["dst_text"] == "译文:你好"
    assert blocks[1]["dst_text"] == ""
    assert len(chat.calls) == 1


def test_translate_paper_incremental_progress(tmp_path: Path) -> None:
    """整篇进度增量推进：已译页不重复调 LLM；fill=False 只查进度。"""
    write_paper(
        tmp_path,
        "p2",
        [
            {"page_no": 1, "blocks": [{"type": "text", "text": "页一"}]},
            {"page_no": 2, "blocks": [{"type": "text", "text": "页二"}]},
        ],
    )
    engine, chat = make_engine(tmp_path)

    r0 = asyncio.run(engine.translate_paper("p2", "zh2en", fill=False))
    assert r0["translated_pages"] == 0 and not r0["done"]

    r1 = asyncio.run(engine.translate_paper("p2", "zh2en", fill=False))
    assert r1["translated_pages"] == 0
    assert len(chat.calls) == 0  # fill=False 绝不触达 LLM

    r2 = asyncio.run(engine.translate_paper("p2", "zh2en", fill=True))
    assert r2["translated_pages"] == 2 and r2["done"]
    assert len(chat.calls) == 2

    r3 = asyncio.run(engine.translate_paper("p2", "zh2en", fill=True))
    assert r3["translated_pages"] == 2 and r3["done"]
    assert len(chat.calls) == 2  # 已译页不重复调 LLM
    assert [p["page_no"] for p in r3["pages"]] == [1, 2]


def test_unknown_paper_raises(tmp_path: Path) -> None:
    """未知 paper_id 抛 ParsedPaperNotFoundError（API 层映射 404）。"""
    engine, _ = make_engine(tmp_path)
    with pytest.raises(ParsedPaperNotFoundError):
        asyncio.run(engine.translate_page("nope", 1, "zh2en"))


def test_bad_page_raises(tmp_path: Path) -> None:
    """页码越界抛 PageNotFoundError（API 层映射 404）。"""
    write_paper(tmp_path, "p3", [{"page_no": 1, "blocks": [{"type": "text", "text": "hi"}]}])
    engine, _ = make_engine(tmp_path)
    with pytest.raises(PageNotFoundError):
        asyncio.run(engine.translate_page("p3", 99, "zh2en"))


async def _collect(ait) -> list[str]:
    out = []
    async for item in ait:
        out.append(item)
    return out
