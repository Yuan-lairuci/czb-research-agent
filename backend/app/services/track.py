"""版本追踪服务：arXiv 论文版本追踪（本地记 v_n，检查 v_n+1）。

- add：登记 arxiv_id；id 不带版本号时先查 arXiv 当前版本作为基线。
- run：逐条查 arXiv 当前版本，若大于本地 tracked_version 则列入 updates
  并把 tracked_version 推进到新版本（确认已知晓，下次 run 不重复报告）；
  tracked_version 为空的首条记录只建基线不报更新。
- 存储：SQLite backend/data/recommend/track.db。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.recommend_models import (
    TrackItem,
    TrackRunResponse,
    TrackUpdate,
)
from app.services.arxiv_client import ArxivClient, ArxivError, parse_version

_TRACK_SCHEMA = """CREATE TABLE IF NOT EXISTS tracked (
    arxiv_id TEXT PRIMARY KEY,
    base_id TEXT NOT NULL,
    title TEXT,
    tracked_version INTEGER,
    latest_version INTEGER,
    added_at TEXT NOT NULL,
    last_checked TEXT
)
"""


class TrackStore:
    """追踪列表 SQLite 存储。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_TRACK_SCHEMA)

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
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def upsert(self, item: dict[str, Any]) -> None:
        """插入或覆盖一条追踪记录。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tracked (arxiv_id, base_id, title, tracked_version,"
                " latest_version, added_at, last_checked)"
                " VALUES (:arxiv_id, :base_id, :title, :tracked_version,"
                " :latest_version, :added_at, :last_checked)"
                " ON CONFLICT(arxiv_id) DO UPDATE SET"
                " title=excluded.title, tracked_version=excluded.tracked_version,"
                " latest_version=excluded.latest_version, last_checked=excluded.last_checked",
                item,
            )

    def get(self, arxiv_id: str) -> dict[str, Any] | None:
        """按原始 arxiv_id 查询。"""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tracked WHERE arxiv_id = ?", (arxiv_id,)).fetchone()
        return dict(row) if row else None

    def get_by_base(self, base_id: str) -> dict[str, Any] | None:
        """按基号查询。"""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tracked WHERE base_id = ?", (base_id,)).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        """全部追踪记录（按登记时间升序）。"""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM tracked ORDER BY added_at").fetchall()
        return [dict(row) for row in rows]


def _row_to_item(row: dict[str, Any]) -> TrackItem:
    return TrackItem(
        arxiv_id=row["arxiv_id"],
        base_id=row["base_id"],
        title=row["title"],
        tracked_version=row["tracked_version"],
        latest_version=row["latest_version"],
        added_at=row["added_at"],
        last_checked=row["last_checked"],
    )


class TrackService:
    """追踪业务：登记、列表、版本巡检。"""

    def __init__(self, store: TrackStore, arxiv: ArxivClient | None = None) -> None:
        self._store = store
        self._arxiv = arxiv if arxiv is not None else ArxivClient()

    def add(self, arxiv_id: str) -> TrackItem:
        """登记追踪；同基号已登记则幂等返回原条目。

        id 不带版本号时查 arXiv 取当前版本作基线（失败则 tracked_version=None，
        由首次 run() 建基线）。
        """
        base, version = parse_version(arxiv_id)
        existing = self._store.get(arxiv_id) or self._store.get_by_base(base)
        if existing is not None:
            return _row_to_item(existing)
        title = None
        if version is None:
            try:
                meta = self._arxiv.get_paper(base)
                if meta:
                    title = meta.get("title")
                    version = meta.get("version")
            except ArxivError:
                version = None  # 离线可用：首巡检建基线
        row = {
            "arxiv_id": arxiv_id,
            "base_id": base,
            "title": title,
            "tracked_version": version,
            "latest_version": version,
            "added_at": TrackStore._now(),
            "last_checked": None,
        }
        self._store.upsert(row)
        return _row_to_item(row)

    def list_items(self) -> list[TrackItem]:
        """追踪列表。"""
        return [_row_to_item(row) for row in self._store.list_all()]

    def run(self) -> TrackRunResponse:
        """版本巡检：检测 v_n -> v_n+1 并列出更新。"""
        updates: list[TrackUpdate] = []
        errors: list[str] = []
        checked = 0
        for row in self._store.list_all():
            checked += 1
            try:
                meta = self._arxiv.get_paper(row["base_id"])
            except ArxivError as exc:
                errors.append(f"{row['base_id']}: {exc}")
                continue
            if meta is None:
                errors.append(f"{row['base_id']}: not found on arXiv")
                continue
            try:
                _, latest = parse_version(meta["arxiv_id"])
            except ValueError as exc:
                errors.append(f"{row['base_id']}: {exc}")
                continue
            latest = latest or 1
            tracked = row["tracked_version"]
            new_row = {
                **row,
                "title": row["title"] or meta.get("title"),
                "latest_version": latest,
                "last_checked": TrackStore._now(),
            }
            if tracked is None:
                new_row["tracked_version"] = latest  # 首巡检建基线
            elif latest > tracked:
                updates.append(
                    TrackUpdate(
                        arxiv_id=row["arxiv_id"],
                        base_id=row["base_id"],
                        title=new_row["title"],
                        old_version=tracked,
                        new_version=latest,
                    )
                )
                new_row["tracked_version"] = latest  # 确认已知晓
            self._store.upsert(new_row)
        return TrackRunResponse(checked=checked, updates=updates, errors=errors)
