"""推荐服务：双源检索合并、引用“含金量”过滤、推荐排序、反馈与 PDF 入库。

- search_merged：S2 + arXiv 双源候选合并（按 arxiv_id/DOI 归并，标 source）；
  S2 不可达时降级为 arXiv 单源并标 source="arxiv_only"，不抛 500。
- filter_valuable_references：引用含金量启发式，剔除“应付式引用”。
- recommend_by_paper：基于种子论文 references/citations 出推荐，排序分 =
  引用量权重 + 新近度 + GitHub 代码加分 + 用户点赞/点踩反馈加权，每条附理由。
- fetch_and_ingest：下载 PDF 并走模块一入库解析。
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.recommend_models import (
    ByPaperResponse,
    Candidate,
    FeedbackResponse,
    FetchResponse,
    Recommendation,
    SearchResponse,
)
from app.services.arxiv_client import ArxivClient, ArxivError, parse_version
from app.services.s2_client import S2Client, S2Error

# ---------------------------------------------------------------------------
# 可配参数（阈值/权重）
# ---------------------------------------------------------------------------

DEFAULT_MIN_CONTEXT_CHARS = 40  # “应付式引用” context 极短阈值
CITATION_WEIGHT = 1.0  # 引用量权重（log10(1+citations)）
RECENCY_WEIGHT = 1.0  # 新近度权重（十年内线性衰减）
GITHUB_BONUS = 0.5  # 检出 GitHub 代码仓库的固定加分
FEEDBACK_WEIGHT = 1.0  # 每条点赞/点踩反馈的加/降权
RECENCY_DECAY_YEARS = 10  # 新近度衰减窗口（年）

_GITHUB_RE = re.compile(
    r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+"
)


# ---------------------------------------------------------------------------
# 引用“含金量”过滤（赛题点名功能）
# ---------------------------------------------------------------------------


def is_perfunctory(
    contexts: list[str],
    intents: list[str],
    min_context_chars: int = DEFAULT_MIN_CONTEXT_CHARS,
) -> bool:
    """判断一次引用是否为“应付式引用”（启发式规则，技术文档素材）。

    规则：判“应付式”必须同时满足——
    1. 意图（S2 intents）全部仅为 background，即引用只作背景罗列，
       没有 methodological / result 等实质使用；
    2. 所有 context 文本长度均小于 min_context_chars（默认 40 字符），
       即引用处几乎没有展开讨论。

    任一条件不满足即保留：出现非 background 意图、任一 context 达到阈值、
    或 intents / contexts 缺失（证据不足，保守保留，不误杀）。
    """
    if not contexts or not intents:
        return False
    if any(intent != "background" for intent in intents):
        return False
    return all(len(ctx.strip()) < min_context_chars for ctx in contexts)


def filter_valuable_references(
    refs: list[dict[str, Any]],
    min_context_chars: int = DEFAULT_MIN_CONTEXT_CHARS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按含金量过滤引用条目，返回 (保留, 剔除)。

    输入为 S2Client.get_references / get_citations 的条目形状：
    {"paper": {...}, "contexts": [str], "intents": [str]}。
    """
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for ref in refs:
        if is_perfunctory(ref.get("contexts", []), ref.get("intents", []), min_context_chars):
            dropped.append(ref)
        else:
            kept.append(ref)
    return kept, dropped


# ---------------------------------------------------------------------------
# GitHub 代码链接检出（S2 externalIds / url / openAccessPdf 等字段）
# ---------------------------------------------------------------------------


def find_github_url(payload: Any) -> str | None:
    """递归扫描 S2 元数据里的字符串值，检出 github.com 仓库链接。"""
    if isinstance(payload, str):
        m = _GITHUB_RE.search(payload)
        return m.group(0) if m else None
    if isinstance(payload, dict):
        for value in payload.values():
            found = find_github_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = find_github_url(value)
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# 反馈存储（SQLite：backend/data/recommend/feedback.db）
# ---------------------------------------------------------------------------

_FEEDBACK_SCHEMA = """CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key TEXT NOT NULL,
    s2_id TEXT,
    arxiv_id TEXT,
    is_like INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
"""


class FeedbackStore:
    """点赞/点踩反馈存储；paper_key 优先 s2_id，其次 arxiv_id。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_FEEDBACK_SCHEMA)

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
    def _key(s2_id: str | None, arxiv_id: str | None) -> str:
        return s2_id or arxiv_id or ""

    def record(self, s2_id: str | None, arxiv_id: str | None, like: bool) -> FeedbackResponse:
        """落一条反馈。"""
        key = self._key(s2_id, arxiv_id)
        if not key:
            raise ValueError("s2_id or arxiv_id required")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO feedback (paper_key, s2_id, arxiv_id, is_like, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (key, s2_id, arxiv_id, 1 if like else 0, datetime.now(UTC).isoformat()),
            )
        return FeedbackResponse(stored=True, paper_key=key)

    def delta_for(self, s2_id: str | None, arxiv_id: str | None) -> int:
        """某论文的反馈净和：点赞 +1 / 点踩 -1 累计。"""
        clauses: list[str] = []
        params: list[str] = []
        for value in (s2_id, arxiv_id):
            if value:
                clauses.append("(paper_key = ? OR s2_id = ? OR arxiv_id = ?)")
                params.extend([value, value, value])
        if not clauses:
            return 0
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(is_like * 2 - 1), 0) AS delta FROM feedback WHERE "
                + " OR ".join(clauses),
                params,
            ).fetchone()
        return int(row["delta"])


# ---------------------------------------------------------------------------
# 排序打分
# ---------------------------------------------------------------------------


def score_entry(
    payload: dict[str, Any],
    feedback_delta: int = 0,
    now_year: int | None = None,
) -> tuple[float, list[str]]:
    """单条候选打分：引用量 + 新近度 + GitHub 加分 + 反馈加权，返回 (score, reasons)。"""
    citations = int(payload.get("citationCount") or 0)
    influential = int(payload.get("influentialCitationCount") or 0)
    score = CITATION_WEIGHT * math.log10(1 + citations)
    reasons: list[str] = [f"被引 {citations} 次"]
    if influential:
        reasons[0] += f"（含 {influential} 次高影响引用）"

    year = payload.get("year")
    if year:
        current = now_year if now_year is not None else datetime.now(UTC).year
        recency = RECENCY_WEIGHT * max(0.0, 1 - (current - int(year)) / RECENCY_DECAY_YEARS)
        if recency > 0:
            score += recency
            reasons.append(f"{int(year)} 年较新工作（新近度加成 {recency:.2f}）")

    github = find_github_url(payload)
    if github:
        score += GITHUB_BONUS
        reasons.append(f"提供 GitHub 代码仓库：{github}")

    if feedback_delta > 0:
        score += FEEDBACK_WEIGHT * feedback_delta
        reasons.append("来自你的点赞反馈加权")
    elif feedback_delta < 0:
        score += FEEDBACK_WEIGHT * feedback_delta
        reasons.append("来自你的点踩反馈降权")
    return score, reasons


# ---------------------------------------------------------------------------
# 双源检索合并
# ---------------------------------------------------------------------------


def _candidate_from_s2(item: dict[str, Any]) -> Candidate:
    ext = item.get("externalIds") or {}
    arxiv_id = ext.get("ArXiv")
    return Candidate(
        title=item.get("title") or "",
        abstract=item.get("abstract"),
        year=item.get("year"),
        citation_count=item.get("citationCount"),
        influential_citation_count=item.get("influentialCitationCount"),
        s2_id=item.get("paperId"),
        arxiv_id=arxiv_id,
        doi=ext.get("DOI"),
        pdf_url=(item.get("openAccessPdf") or {}).get("url"),
        github_url=find_github_url(item),
        source="s2",
    )


def _candidate_from_arxiv(item: dict[str, Any]) -> Candidate:
    published = item.get("published") or ""
    return Candidate(
        title=item.get("title") or "",
        abstract=item.get("abstract"),
        year=int(published[:4]) if published[:4].isdigit() else None,
        s2_id=None,
        arxiv_id=item.get("base_id"),
        pdf_url=item.get("pdf_url"),
        source="arxiv",
    )


def _arxiv_base(arxiv_id: str) -> str:
    """去掉版本后缀（1706.03762v7 -> 1706.03762），保留旧式 id 的斜杠。"""
    return re.sub(r"v\d+$", "", arxiv_id)


def _merge_key(cand: Candidate) -> str:
    """合并归一键：arxiv_id 基号 > DOI > s2_id > 标题。"""
    if cand.arxiv_id:
        return f"arxiv:{_arxiv_base(cand.arxiv_id)}"
    if cand.doi:
        return f"doi:{cand.doi.lower()}"
    if cand.s2_id:
        return f"s2:{cand.s2_id}"
    return f"title:{cand.title.lower()}"


def search_merged(
    query: str,
    limit: int = 10,
    *,
    s2: S2Client | None = None,
    arxiv: ArxivClient | None = None,
) -> SearchResponse:
    """双源检索合并；S2 不可达降级为 arxiv_only，绝不 500。"""
    s2_client = s2 if s2 is not None else S2Client()
    arxiv_client = arxiv if arxiv is not None else ArxivClient()

    s2_ok = True
    arxiv_ok = True
    s2_items: list[dict[str, Any]] = []
    arxiv_items: list[dict[str, Any]] = []
    try:
        s2_items = s2_client.search(query, limit=limit)
    except (S2Error, ArxivError, OSError, ValueError):
        s2_ok = False
    try:
        arxiv_items = arxiv_client.search(query, max_results=limit)
    except (ArxivError, OSError, ValueError):
        arxiv_ok = False
    if not s2_ok and not arxiv_ok:
        raise RuntimeError("both S2 and arXiv are unreachable")

    merged: dict[str, Candidate] = {}
    for item in arxiv_items:
        cand = _candidate_from_arxiv(item)
        merged[_merge_key(cand)] = cand
    for item in s2_items:
        cand = _candidate_from_s2(item)
        key = _merge_key(cand)
        if key in merged:
            base = merged[key]
            merged[key] = base.model_copy(
                update={
                    "source": "both",
                    "title": base.title or cand.title,
                    "abstract": base.abstract or cand.abstract,
                    "year": base.year or cand.year,
                    "citation_count": cand.citation_count,
                    "influential_citation_count": cand.influential_citation_count,
                    "s2_id": cand.s2_id,
                    "doi": cand.doi,
                    "pdf_url": base.pdf_url or cand.pdf_url,
                    "github_url": base.github_url or cand.github_url,
                }
            )
        else:
            merged[key] = cand

    results = list(merged.values())
    if not s2_ok:
        for cand in results:
            cand.source = "arxiv_only"
    # 双源命中优先，其次按被引量降序
    results.sort(key=lambda c: (c.source != "both", -(c.citation_count or 0), c.title))
    return SearchResponse(
        query=query,
        results=results[:limit],
        degraded=not s2_ok,
        s2_ok=s2_ok,
        arxiv_ok=arxiv_ok,
    )


# ---------------------------------------------------------------------------
# 下载 PDF 并走模块一入库解析
# ---------------------------------------------------------------------------


def fetch_and_ingest(
    arxiv_id: str,
    *,
    arxiv: ArxivClient | None = None,
    data_dir: Path | str | None = None,
) -> FetchResponse:
    """下载 PDF 并走模块一入库解析。

    入库方式选型（二选一，理由）：直接 import app.services.pdf_parse 与
    app.models.paper_models.PaperDB 复用模块一解析与元数据存储，而非 HTTP 调
    自身 POST /api/library/papers——无需起服务即可测试，数据目录可注入，
    且与 upload_paper 落盘布局完全一致（library/{paper_id}.pdf、
    parsed/{paper_id}.json、library/library.db），GET /api/library/papers 可见。
    """
    from app.models.paper_models import PaperDB
    from app.services import pdf_parse

    if data_dir is not None:
        data_root = Path(data_dir)
    else:
        data_root = Path(__file__).resolve().parents[2] / "data"
    library_dir = data_root / "library"
    parsed_dir = data_root / "parsed"
    library_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    client = arxiv if arxiv is not None else ArxivClient()
    payload = client.download_pdf(arxiv_id)  # 网络失败抛 ArxivError

    sha256 = hashlib.sha256(payload).hexdigest()
    paper_id = sha256[:12]
    db = PaperDB(library_dir / "library.db")
    existing = db.get(paper_id)
    if existing is not None:
        return FetchResponse(
            arxiv_id=arxiv_id,
            paper_id=paper_id,
            title=existing["title"],
            num_pages=existing["num_pages"],
            parse_status="duplicate",
            detail="already in library",
        )

    saved = library_dir / f"{paper_id}.pdf"
    saved.write_bytes(payload)

    parse_status = "parsed"
    detail = None
    title = f"arxiv:{arxiv_id}"
    num_pages = 0
    try:
        contract = pdf_parse.parse_pdf(saved, source_filename=f"{arxiv_id}.pdf")
        pdf_parse.save_parsed(contract, parsed_dir)
        title = contract["title"]
        num_pages = contract["num_pages"]
    except Exception as exc:  # noqa: BLE001 - 解析失败保留文件与记录（同 upload_paper）
        parse_status = "failed"
        detail = f"parse failed: {exc}"

    db.insert(
        {
            "paper_id": paper_id,
            "title": title,
            "num_pages": num_pages,
            "parse_status": parse_status,
            "dedup_group": None,
            "source_filename": f"{arxiv_id}.pdf",
            "sha256": sha256,
            "uploaded_at": PaperDB.now_iso(),
        }
    )
    return FetchResponse(
        arxiv_id=arxiv_id,
        paper_id=paper_id,
        title=title,
        num_pages=num_pages,
        parse_status=parse_status,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# 基于单篇论文的推荐
# ---------------------------------------------------------------------------


def recommend_by_paper(
    paper_id: str,
    top_k: int = 10,
    *,
    s2: S2Client | None = None,
    store: FeedbackStore | None = None,
    min_context_chars: int = DEFAULT_MIN_CONTEXT_CHARS,
    now_year: int | None = None,
) -> ByPaperResponse:
    """基于种子论文 references/citations 出推荐。

    先做引用含金量过滤（剔除应付式引用），再按 排序分 = 引用量权重 +
    新近度 + GitHub 代码加分 + 反馈加权 排序取 top_k，每条附推荐理由文本。
    """
    from app.services.arxiv_client import is_bare_arxiv_id

    client = s2 if s2 is not None else S2Client()
    refs = client.get_references(paper_id)
    cits = client.get_citations(paper_id)

    kept_refs, dropped_refs = filter_valuable_references(refs, min_context_chars)
    kept_cits, dropped_cits = filter_valuable_references(cits, min_context_chars)

    seed_base = None
    try:
        if is_bare_arxiv_id(paper_id):
            seed_base = parse_version(paper_id)[0]
    except ValueError:
        seed_base = None

    merged: dict[str, tuple[dict[str, Any], str]] = {}
    for relation, entries in (("reference", kept_refs), ("citation", kept_cits)):
        for edge in entries:
            payload = edge.get("paper") or {}
            if not payload.get("title"):
                continue
            ext = payload.get("externalIds") or {}
            entry_arxiv = ext.get("ArXiv")
            if entry_arxiv and seed_base and _arxiv_base(entry_arxiv) == seed_base:
                continue  # 种子论文自身不推荐
            key = (
                f"s2:{payload.get('paperId')}"
                if payload.get("paperId")
                else f"arxiv:{entry_arxiv}"
                if entry_arxiv
                else f"title:{payload['title'].lower()}"
            )
            if key not in merged:
                merged[key] = (payload, relation)

    recommendations: list[Recommendation] = []
    for payload, relation in merged.values():
        ext = payload.get("externalIds") or {}
        feedback_delta = store.delta_for(payload.get("paperId"), ext.get("ArXiv")) if store else 0
        score, reasons = score_entry(payload, feedback_delta, now_year=now_year)
        if relation == "reference":
            relation_reason = "来自种子论文的参考文献"
        else:
            relation_reason = "来自引用种子论文的文献"
        recommendations.append(
            Recommendation(
                title=payload.get("title") or "",
                s2_id=payload.get("paperId"),
                arxiv_id=ext.get("ArXiv"),
                year=payload.get("year"),
                citation_count=payload.get("citationCount"),
                influential_citation_count=payload.get("influentialCitationCount"),
                github_url=find_github_url(payload),
                score=round(score, 4),
                reasons=[relation_reason, *reasons],
                relation=relation,
            )
        )

    recommendations.sort(key=lambda r: (-r.score, r.title))
    return ByPaperResponse(
        paper_id=paper_id,
        recommendations=recommendations[:top_k],
        references_considered=len(refs),
        citations_considered=len(cits),
        dropped_perfunctory=len(dropped_refs) + len(dropped_cits),
    )
