"""翻译缓存存储：SQLite 落盘 backend/data/translate/translate_cache.db。

缓存 schema
-----------
text_cache  整段文本翻译缓存，cache key = sha256(model + direction + src_text)：
    key         TEXT PRIMARY KEY  -- 十六进制 sha256(model+direction+src_text)
    model       TEXT              -- LLM 模型名
    direction   TEXT              -- zh2en / en2zh
    src_text    TEXT              -- 源文本原文
    translated  TEXT              -- 译文（已 unmask 还原）
    created_at  TEXT              -- 写入时间 ISO8601
page_cache  页翻译进度（逐块对齐行），供整篇增量翻译查询：
    paper_id    TEXT              -- 解析契约 paper_id
    direction   TEXT              -- zh2en / en2zh
    page_no     INTEGER           -- 页码（1 起）
    block_index INTEGER           -- 页内块序号（0 起）
    src_text    TEXT              -- 块源文本
    dst_text    TEXT              -- 块译文
    PRIMARY KEY (paper_id, direction, page_no, block_index)
连接策略：每次操作独立开关连接，避免跨线程共享连接的线程安全问题。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def text_cache_key(model: str, direction: str, src_text: str) -> str:
    """cache key = sha256(model + direction + src_text) 的十六进制串。"""
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(direction.encode("utf-8"))
    h.update(src_text.encode("utf-8"))
    return h.hexdigest()


def default_db_path() -> Path:
    """默认库路径：TRANSLATE_CACHE_DIR 环境变量或 backend/data/translate/translate_cache.db。"""
    env = os.environ.get("TRANSLATE_CACHE_DIR")
    if env:
        return Path(env) / "translate_cache.db"
    return Path(__file__).resolve().parents[2] / "data" / "translate" / "translate_cache.db"


class TranslateStore:
    """翻译缓存 SQLite 存储；schema 与连接策略见模块 docstring。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30)

    def _init_schema(self) -> None:
        """建表（幂等）：text_cache 与 page_cache，见模块 docstring。"""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS text_cache (
                    key TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    src_text TEXT NOT NULL,
                    translated TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_cache (
                    paper_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    page_no INTEGER NOT NULL,
                    block_index INTEGER NOT NULL,
                    src_text TEXT NOT NULL,
                    dst_text TEXT NOT NULL,
                    PRIMARY KEY (paper_id, direction, page_no, block_index)
                )
                """
            )

    def get_text(self, model: str, direction: str, src_text: str) -> str | None:
        """命中返回译文，未命中返回 None；命中时调用方不得再触达 LLM。"""
        key = text_cache_key(model, direction, src_text)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT translated FROM text_cache WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def put_text(self, model: str, direction: str, src_text: str, translated: str) -> None:
        """写入（或覆盖）整段文本翻译缓存。"""
        key = text_cache_key(model, direction, src_text)
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO text_cache"
                " (key, model, direction, src_text, translated, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (key, model, direction, src_text, translated, now),
            )

    def save_page_block(
        self,
        paper_id: str,
        direction: str,
        page_no: int,
        block_index: int,
        src_text: str,
        dst_text: str,
    ) -> None:
        """写入（或覆盖）一页中一个块的对齐翻译行，作为整篇进度依据。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO page_cache"
                " (paper_id, direction, page_no, block_index, src_text, dst_text)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (paper_id, direction, page_no, block_index, src_text, dst_text),
            )

    def get_paper_blocks(
        self, paper_id: str, direction: str
    ) -> list[tuple[int, int, str, str]]:
        """返回 (page_no, block_index, src_text, dst_text) 列表，按页码与块序排序。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT page_no, block_index, src_text, dst_text FROM page_cache"
                " WHERE paper_id = ? AND direction = ? ORDER BY page_no, block_index",
                (paper_id, direction),
            ).fetchall()
        return [(int(a), int(b), c, d) for a, b, c, d in rows]
