"""模块一数据模型：ParsedPaper JSON 契约、API 响应模型与 SQLite 元数据存储。

ParsedPaper 契约（模块一生产、模块二消费，形状不得偏离）::

    {"paper_id":"str","title":"str","num_pages":"int",
     "pages":[{"page_no":"int","blocks":[{"type":"text|heading|table|formula|figure",
                                          "text":"str","level":"int(仅heading)"}]}],
     "references":[{"index":"int","raw":"str","title":"str?","arxiv_id":"str?"}],
     "meta":{"sha256":"str","source_filename":"str"}}

约定：heading 块携带 level(int)；其余块不携带 level 键。references 的
title / arxiv_id 两键始终存在，未知时为 null。
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

ARXIV_ID_PATTERN = r"\d{4}\.\d{4,5}"
ARXIV_ID_RE = re.compile(ARXIV_ID_PATTERN)
BLOCK_TYPES = ("text", "heading", "table", "formula", "figure")


class Block(BaseModel):
    """页面内容块。"""

    type: Literal["text", "heading", "table", "formula", "figure"]
    text: str
    level: int | None = Field(default=None, description="标题层级，仅 heading 块携带")


class Page(BaseModel):
    """单页内容。"""

    page_no: int
    blocks: list[Block]


class Reference(BaseModel):
    """参考文献条目。"""

    index: int
    raw: str
    title: str | None = None
    arxiv_id: str | None = None


class Meta(BaseModel):
    """来源元信息。"""

    sha256: str
    source_filename: str


class ParsedPaper(BaseModel):
    """ParsedPaper 契约的强类型表示。"""

    paper_id: str
    title: str
    num_pages: int
    pages: list[Page]
    references: list[Reference]
    meta: Meta

    def to_contract(self) -> dict[str, Any]:
        """序列化为严格符合契约的 dict（heading 块带 level，其余块不带）。"""
        pages: list[dict[str, Any]] = []
        for page in self.pages:
            blocks: list[dict[str, Any]] = []
            for block in page.blocks:
                item: dict[str, Any] = {"type": block.type, "text": block.text}
                if block.type == "heading":
                    item["level"] = block.level if block.level is not None else 1
                blocks.append(item)
            pages.append({"page_no": page.page_no, "blocks": blocks})
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "num_pages": self.num_pages,
            "pages": pages,
            "references": [
                {
                    "index": ref.index,
                    "raw": ref.raw,
                    "title": ref.title,
                    "arxiv_id": ref.arxiv_id,
                }
                for ref in self.references
            ],
            "meta": {
                "sha256": self.meta.sha256,
                "source_filename": self.meta.source_filename,
            },
        }


def validate_contract(data: dict[str, Any]) -> list[str]:
    """严格校验 ParsedPaper 契约形状，返回错误列表（空列表 = 合法）。"""
    errors: list[str] = []
    top_keys = {"paper_id", "title", "num_pages", "pages", "references", "meta"}
    if set(data.keys()) != top_keys:
        errors.append(f"top-level keys {sorted(data.keys())} != {sorted(top_keys)}")
        return errors
    if not isinstance(data["paper_id"], str):
        errors.append("paper_id must be str")
    if not isinstance(data["title"], str):
        errors.append("title must be str")
    if not isinstance(data["num_pages"], int) or isinstance(data["num_pages"], bool):
        errors.append("num_pages must be int")
    for i, page in enumerate(data["pages"]):
        if set(page.keys()) != {"page_no", "blocks"}:
            errors.append(f"pages[{i}] keys {sorted(page.keys())} != ['blocks', 'page_no']")
            continue
        if not isinstance(page["page_no"], int):
            errors.append(f"pages[{i}].page_no must be int")
        for j, block in enumerate(page["blocks"]):
            where = f"pages[{i}].blocks[{j}]"
            keys = set(block.keys())
            if not keys <= {"type", "text", "level"}:
                errors.append(
                    f"{where} has unexpected keys {sorted(keys - {'type', 'text', 'level'})}"
                )
                continue
            if "type" not in keys or block["type"] not in BLOCK_TYPES:
                errors.append(f"{where}.type invalid: {block.get('type')!r}")
                continue
            if "text" not in keys or not isinstance(block["text"], str):
                errors.append(f"{where}.text must be str")
            if block["type"] == "heading":
                level = block.get("level")
                if not isinstance(level, int) or isinstance(level, bool):
                    errors.append(f"{where}: heading must carry int level, got {level!r}")
            elif "level" in keys:
                errors.append(f"{where}: non-heading block must not carry level")
    for i, ref in enumerate(data["references"]):
        keys = set(ref.keys())
        if not keys <= {"index", "raw", "title", "arxiv_id"}:
            errors.append(
                f"references[{i}] has unexpected keys "
                f"{sorted(keys - {'index', 'raw', 'title', 'arxiv_id'})}"
            )
            continue
        if "index" not in ref or not isinstance(ref["index"], int):
            errors.append(f"references[{i}].index must be int")
        if "raw" not in ref or not isinstance(ref["raw"], str):
            errors.append(f"references[{i}].raw must be str")
        for key in ("title", "arxiv_id"):
            if key in ref and ref[key] is not None and not isinstance(ref[key], str):
                errors.append(f"references[{i}].{key} must be str or null")
        if ref.get("arxiv_id") and not ARXIV_ID_RE.fullmatch(ref["arxiv_id"]):
            errors.append(
                f"references[{i}].arxiv_id {ref['arxiv_id']!r} does not match {ARXIV_ID_PATTERN}"
            )
    meta = data["meta"]
    if set(meta.keys()) != {"sha256", "source_filename"}:
        errors.append(f"meta keys {sorted(meta.keys())} != ['sha256', 'source_filename']")
    elif not isinstance(meta["sha256"], str) or not isinstance(meta["source_filename"], str):
        errors.append("meta values must be str")
    return errors


# ---------------------------------------------------------------------------
# API 响应模型（供 pdf_library 路由与测试使用）
# ---------------------------------------------------------------------------


class PaperInfo(BaseModel):
    """文献库条目。"""

    paper_id: str
    title: str
    num_pages: int
    parse_status: str = Field(description="解析状态：pending / parsed / failed")
    dedup_group: str | None = None
    source_filename: str
    sha256: str
    uploaded_at: str


class PaperListResponse(BaseModel):
    """文献列表响应。"""

    papers: list[PaperInfo]


class DedupGroup(BaseModel):
    """去重组。"""

    paper_ids: list[str]
    same_work: bool
    suggested_keep: str
    reason: str


class DedupScanResponse(BaseModel):
    """去重扫描响应。"""

    groups: list[DedupGroup]


class OcrResult(BaseModel):
    """OCR 结果（引擎缺失时 needs_ocr=True, engine="none"）。"""

    paper_id: str
    page_no: int
    needs_ocr: bool
    engine: str
    text: str = ""


# ---------------------------------------------------------------------------
# SQLite 元数据存储（stdlib sqlite3，库文件位于 backend/data/library/ 下）
# ---------------------------------------------------------------------------

_SCHEMA = """CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    num_pages INTEGER NOT NULL,
    parse_status TEXT NOT NULL,
    dedup_group TEXT,
    source_filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
)
"""


class PaperDB:
    """文献元数据库（每行一篇论文）。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def now_iso() -> str:
        """当前 UTC 时间（ISO 格式）。"""
        return datetime.now(UTC).isoformat()

    def insert(self, record: dict[str, Any]) -> None:
        """插入一条文献记录。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO papers (paper_id, title, num_pages, parse_status, dedup_group,"
                " source_filename, sha256, uploaded_at)"
                " VALUES (:paper_id, :title, :num_pages, :parse_status, :dedup_group,"
                " :source_filename, :sha256, :uploaded_at)",
                {"dedup_group": None, **record},
            )

    def list_all(self) -> list[dict[str, Any]]:
        """按上传时间倒序返回全部文献记录。"""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM papers ORDER BY uploaded_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get(self, paper_id: str) -> dict[str, Any] | None:
        """按 paper_id 查询单条记录。"""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
        return dict(row) if row else None

    def delete(self, paper_id: str) -> bool:
        """删除记录，返回是否命中。"""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
        return cur.rowcount > 0

    def clear_dedup_groups(self) -> None:
        """清空所有去重组标记。"""
        with self._conn() as conn:
            conn.execute("UPDATE papers SET dedup_group = NULL")

    def set_dedup_group(self, paper_id: str, group: str) -> None:
        """写入去重组标记。"""
        with self._conn() as conn:
            conn.execute("UPDATE papers SET dedup_group = ? WHERE paper_id = ?", (group, paper_id))
