"""参考文献生成（真实无幻觉）：条目内容只能来自可核实来源。

来源三类（均为实查/实据，不允许任何生成内容）：
1. 语料 backend/data/parsed/{paper_id}.json 的 references（模块一解析契约）；
2. s2_client 实查（搜索 / 论文详情 / 参考文献列表）；
3. arxiv_client 实查（搜索 / 论文元数据）。

LLM 只做条目筛选与格式化。输出前逐条与来源核对：
- 查无此 id（arxiv_id/doi 不在来源池）→ 剔除，注明原因；
- 题名对不上（LLM 题名与来源题名不一致）→ 剔除，注明原因；
- 核上的条目内容整体替换为来源记录，杜绝任何 LLM 生成字段。
LLM 不可用（无 key / 调用失败 / 输出非法）时降级为纯来源确定性输出，
保证真跑链路（无 LLM key）也能产出真实文献。
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.writing_models import (
    DroppedReference,
    ReferenceItem,
    ReferencesResponse,
    Style,
)
from app.services.arxiv_client import ArxivClient, ArxivError, parse_version
from app.services.chat_service import ChatService, ChatServiceError, chat_service
from app.services.s2_client import S2Client, S2Error

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_ARXIV_IN_TEXT_RE = re.compile(r"\b(\d{4}\.\d{4,5})\b")
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


class ReferencesError(RuntimeError):
    """参考文献生成失败（输入非法等）。"""


class SourcesUnavailableError(ReferencesError):
    """所有文献来源均不可用且没有可用条目。"""


@dataclass(frozen=True)
class Candidate:
    """一条可核实来源记录：字段全部来自来源，代码侧不做任何生成。"""

    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    arxiv_id: str | None = None
    doi: str | None = None
    url: str | None = None
    source: str = ""
    raw: str = ""


def norm_title(title: str) -> str:
    """题名归一化：小写、去标点空白（保留中英文字符），用于对得上/对不上判断。"""
    return re.sub(r"[\W_]+", "", (title or "").lower(), flags=re.UNICODE)


def norm_arxiv(arxiv_id: str) -> str:
    """arxiv_id 归一化：剥掉版本号得到基号（1706.03762v7 -> 1706.03762）。"""
    try:
        base, _ = parse_version(arxiv_id)
        return base
    except ValueError:
        return (arxiv_id or "").strip()


def parse_raw_reference(
    raw: str, fallback_title: str | None = None
) -> tuple[str, tuple[str, ...], int | None]:
    """语料 raw 引用行的启发式解析（尽力而为，内容仍逐字来自 raw）。

    形如 "[1] Alice A, Bob B. Great Title. Venue, 2020." -> 标题/作者/年份。
    解析不出结构时整体退化为题名，宁可粗糙也不引入外部内容。
    """
    text = re.sub(r"^\s*(?:\[\d+\]|\d+[.)])\s*", "", (raw or "")).strip()
    year_m = _YEAR_RE.search(text)
    year = int(year_m.group(1)) if year_m else None
    parts = [p.strip() for p in text.split(". ") if p.strip()]
    if len(parts) >= 2:
        authors = tuple(a.strip() for a in re.split(r",| and ", parts[0]) if a.strip())
        title = parts[1].rstrip(".")
    else:
        authors = ()
        title = text or (fallback_title or "")
    return title, authors, year


class SourcePool:
    """去重后的来源池；提供按 arxiv_id / doi / 题名的核对查询。"""

    def __init__(self) -> None:
        self.candidates: list[Candidate] = []
        self._by_arxiv: dict[str, Candidate] = {}
        self._by_doi: dict[str, Candidate] = {}
        self._by_title: dict[str, Candidate] = {}
        self._keys: set[str] = set()

    def add(self, cand: Candidate) -> None:
        """入池；同一 arxiv_id/doi/题名 只保留首见记录。"""
        key = norm_arxiv(cand.arxiv_id) if cand.arxiv_id else (
            (cand.doi or "").lower() if cand.doi else norm_title(cand.title)
        )
        if not key or key in self._keys:
            return
        self._keys.add(key)
        self.candidates.append(cand)
        if cand.arxiv_id:
            self._by_arxiv[norm_arxiv(cand.arxiv_id)] = cand
        if cand.doi:
            self._by_doi[cand.doi.lower()] = cand
        self._by_title[norm_title(cand.title)] = cand

    def by_arxiv(self, base_id: str) -> Candidate | None:
        return self._by_arxiv.get(base_id)

    def by_doi(self, doi: str) -> Candidate | None:
        return self._by_doi.get(doi.lower())

    def by_title(self, normed: str) -> Candidate | None:
        return self._by_title.get(normed)


def resolve_entry(entry: dict[str, Any], pool: SourcePool) -> tuple[Candidate | None, str | None]:
    """把 LLM 条目核对回来源池：核上返回 (来源记录, None)，核不上返回 (None, 原因)。"""
    arxiv_id = (entry.get("arxiv_id") or "").strip() or None
    doi = (entry.get("doi") or "").strip() or None
    title = (entry.get("title") or "").strip()
    cand: Candidate | None = None
    if arxiv_id:
        cand = pool.by_arxiv(norm_arxiv(arxiv_id))
        if cand is None:
            return None, f"查无此 id：arxiv_id {arxiv_id} 不在可核实来源中"
    elif doi:
        cand = pool.by_doi(doi)
        if cand is None:
            return None, f"查无此 id：doi {doi} 不在可核实来源中"
    elif title:
        cand = pool.by_title(norm_title(title))
        if cand is None:
            return None, f"查无此题名：{title} 不在可核实来源中"
    else:
        return None, "缺少可核实标识（arxiv_id/doi/题名均缺失）"
    if title:
        want, have = norm_title(title), norm_title(cand.title)
        if want != have and want not in have and have not in want:
            return None, f"题名对不上：LLM='{title}' 来源='{cand.title}'"
    return cand, None


def format_reference(cand: Candidate, style: Style) -> str:
    """确定性格式化（内容全部来自来源记录）。"""
    authors = ", ".join(cand.authors)
    year = str(cand.year) if cand.year else "n.d."
    ident = f"arXiv:{cand.arxiv_id}" if cand.arxiv_id else (f"doi:{cand.doi}" if cand.doi else "")
    venue = cand.venue or ""
    tail = ". ".join(x for x in (venue, ident) if x)
    if style == "apa":
        head = f"{authors or cand.title} ({year}). {cand.title if authors else ''}".strip()
        parts = [head] + [tail] + [cand.url or ""]
    elif style == "gbt7714":
        parts = [authors or cand.title, cand.title if authors else "", tail, year]
    else:
        parts = [authors, f"({year})", cand.title, tail]
    return ". ".join(p.rstrip(".") for p in parts if p).rstrip(".") + "."


def s2_record_to_candidate(rec: dict[str, Any], source: str) -> Candidate | None:
    """S2 论文记录 -> 来源记录（PAPER_FIELDS / CITE_FIELDS 形状）。"""
    title = (rec.get("title") or "").strip()
    if not title:
        return None
    ext = rec.get("externalIds") or {}
    arxiv_id = ext.get("ArXiv") or None
    doi = ext.get("DOI") or None
    authors = tuple(
        a.get("name", "").strip() for a in (rec.get("authors") or []) if a.get("name")
    )
    year = rec.get("year") if isinstance(rec.get("year"), int) else None
    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else (rec.get("url") or None)
    return Candidate(
        title=title,
        authors=authors,
        year=year,
        venue=(rec.get("venue") or "").strip() or None,
        arxiv_id=arxiv_id,
        doi=doi,
        url=url,
        source=source,
    )


def arxiv_record_to_candidate(rec: dict[str, Any], source: str) -> Candidate | None:
    """arXiv Atom 元数据 -> 来源记录（_entry_to_meta 形状）。"""
    title = (rec.get("title") or "").strip()
    arxiv_id = rec.get("arxiv_id") or ""
    if not title or not arxiv_id:
        return None
    published = rec.get("published") or ""
    year = int(published[:4]) if published[:4].isdigit() else None
    return Candidate(
        title=title,
        authors=tuple(a.strip() for a in (rec.get("authors") or []) if a.strip()),
        year=year,
        venue="arXiv",
        arxiv_id=arxiv_id,
        doi=None,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        source=source,
    )


def corpus_refs_to_candidates(paper_id: str, refs: list[dict[str, Any]]) -> list[Candidate]:
    """语料 references（解析契约 {index, raw, title, arxiv_id}）-> 来源记录列表。"""
    out: list[Candidate] = []
    for ref in refs:
        raw = ref.get("raw") or ""
        title, authors, year = parse_raw_reference(raw, ref.get("title"))
        arxiv_id = (ref.get("arxiv_id") or "").strip() or None
        if not title and not arxiv_id:
            continue
        out.append(
            Candidate(
                title=title,
                authors=authors,
                year=year,
                venue=None,
                arxiv_id=arxiv_id,
                doi=None,
                url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
                source=f"corpus:{paper_id}#{ref.get('index')}",
                raw=raw,
            )
        )
    return out


@dataclass
class _CollectResult:
    """来源收集结果。"""

    pool: SourcePool = field(default_factory=SourcePool)
    sources_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ReferencesEngine:
    """注入式参考文献引擎（chat/s2/arxiv/parsed_dir 均可注入，独立可测零网络）。"""

    def __init__(
        self,
        chat: ChatService | None = None,
        s2: S2Client | None = None,
        arxiv: ArxivClient | None = None,
        parsed_dir: str | Path | None = None,
        source_limit: int = 10,
    ) -> None:
        self._chat = chat if chat is not None else chat_service
        self._s2 = s2 if s2 is not None else S2Client()
        self._arxiv = arxiv if arxiv is not None else ArxivClient()
        self._parsed_dir = (
            Path(parsed_dir) if parsed_dir is not None else Path(__file__).resolve().parents[2] / "data" / "parsed"
        )
        self._source_limit = source_limit

    def _collect(self, topic: str | None, paper_ids: list[str] | None) -> _CollectResult:
        """从三类来源收集候选（单来源失败只记错，不影响其他来源）。"""
        res = _CollectResult()
        for pid in paper_ids or []:
            parsed = self._parsed_dir / f"{pid}.json"
            if parsed.exists():
                try:
                    data = json.loads(parsed.read_text(encoding="utf-8"))
                    for cand in corpus_refs_to_candidates(pid, data.get("references") or []):
                        res.pool.add(cand)
                    res.sources_used.append(f"corpus:{pid}")
                except (OSError, ValueError) as exc:
                    res.errors.append(f"corpus:{pid} 读取失败: {exc}")
            try:
                for edge in self._s2.get_references(pid, limit=self._source_limit):
                    cand = s2_record_to_candidate(
                        edge.get("paper") or {}, f"s2:references:{pid}"
                    )
                    if cand:
                        res.pool.add(cand)
                res.sources_used.append(f"s2:references:{pid}")
            except S2Error as exc:
                res.errors.append(f"s2:references:{pid} 失败: {exc}")
        if topic:
            try:
                for rec in self._s2.search(topic, limit=self._source_limit):
                    cand = s2_record_to_candidate(rec, "s2:search")
                    if cand:
                        res.pool.add(cand)
                res.sources_used.append("s2:search")
            except S2Error as exc:
                res.errors.append(f"s2:search 失败: {exc}")
            try:
                for rec in self._arxiv.search(
                    topic, max_results=self._source_limit, field="all"
                ):
                    cand = arxiv_record_to_candidate(rec, "arxiv:search")
                    if cand:
                        res.pool.add(cand)
                res.sources_used.append("arxiv:search")
            except ArxivError as exc:
                res.errors.append(f"arxiv:search 失败: {exc}")
        return res

    @staticmethod
    def _build_messages(
        topic: str | None,
        paper_ids: list[str] | None,
        style: Style,
        pool: SourcePool,
    ) -> list[dict[str, str]]:
        """LLM 只做筛选与格式化：候选清单是唯一允许引用的条目集合。"""
        listing = [
            {
                "key": c.arxiv_id or c.doi or c.title,
                "title": c.title,
                "authors": list(c.authors),
                "year": c.year,
                "venue": c.venue,
                "arxiv_id": c.arxiv_id,
                "doi": c.doi,
                "url": c.url,
            }
            for c in pool.candidates
        ]
        system = (
            "你是学术文献整理助手。下面给出一批【已核实】的候选文献清单，"
            "你只能从中筛选条目并按指定风格格式化，绝对不允许虚构新条目，"
            "也不允许修改任何字段内容。输出 JSON 数组，每项形如 "
            '{"title": "...", "authors": [...], "year": 2017, "venue": "...", '
            '"arxiv_id": "...", "doi": null, "url": "...", "formatted": "..."}，'
            f"formatted 为 {style} 风格的引用条目文本。"
            "只输出 JSON 数组本身，不要任何解释或 Markdown 围栏。"
        )
        who = f"主题：{topic}" if topic else f"论文：{', '.join(paper_ids or [])}"
        user = f"{who}\n风格：{style}\n候选清单（唯一合法来源）：\n{json.dumps(listing, ensure_ascii=False)}"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    async def _llm_entries(
        self,
        topic: str | None,
        paper_ids: list[str] | None,
        style: Style,
        pool: SourcePool,
    ) -> list[dict[str, Any]] | None:
        """LLM 筛选 + 格式化；任何失败返回 None（触发确定性降级）。"""
        try:
            raw = await self._chat.chat(
                self._build_messages(topic, paper_ids, style, pool)
            )
        except (ChatServiceError, OSError):
            return None
        try:
            parsed = json.loads(_FENCE_RE.search(raw).group(1) if _FENCE_RE.search(raw) else raw.strip())
        except (json.JSONDecodeError, AttributeError):
            return None
        if not isinstance(parsed, list):
            return None
        return [e for e in parsed if isinstance(e, dict)]

    @staticmethod
    def _to_item(cand: Candidate, style: Style, llm_formatted: Any) -> ReferenceItem:
        """来源记录 -> 输出条目；LLM 格式串必须含来源题名与年份才被采纳。"""
        formatted = format_reference(cand, style)
        if isinstance(llm_formatted, str) and llm_formatted.strip():
            normed = norm_title(llm_formatted)
            if norm_title(cand.title) in normed and (
                cand.year is None or str(cand.year) in llm_formatted
            ):
                formatted = llm_formatted.strip()
        return ReferenceItem(
            title=cand.title,
            authors=list(cand.authors),
            year=cand.year,
            venue=cand.venue,
            arxiv_id=cand.arxiv_id,
            doi=cand.doi,
            url=cand.url,
            source=cand.source,
            formatted=formatted,
        )

    async def build_references(
        self,
        topic: str | None = None,
        paper_ids: list[str] | None = None,
        style: Style = "plain",
        limit: int = 10,
    ) -> ReferencesResponse:
        """生成真实无幻觉参考文献：来源收集 -> LLM 筛选 -> 逐条核对 -> 输出。"""
        if not (topic and topic.strip()) and not paper_ids:
            raise ReferencesError("topic 与 paper_ids 至少提供一个")
        res = await asyncio.to_thread(self._collect, topic, paper_ids)
        query = (topic or "").strip() or ", ".join(paper_ids or [])
        if not res.pool.candidates:
            if res.errors:
                raise SourcesUnavailableError(
                    "所有文献来源均不可用: " + "; ".join(res.errors)
                )
            return ReferencesResponse(
                query=query,
                style=style,
                references=[],
                dropped=[],
                sources_used=res.sources_used,
            )
        entries = await self._llm_entries(topic, paper_ids, style, res.pool)
        references: list[ReferenceItem] = []
        dropped: list[DroppedReference] = []
        if entries is None:
            # LLM 不可用：降级为纯来源确定性输出（条目天然可核实）
            references = [
                self._to_item(c, style, None) for c in res.pool.candidates[:limit]
            ]
        else:
            seen: set[str] = set()
            for entry in entries:
                cand, reason = resolve_entry(entry, res.pool)
                if cand is None:
                    dropped.append(
                        DroppedReference(
                            title=(entry.get("title") or "").strip() or None,
                            arxiv_id=(entry.get("arxiv_id") or "").strip() or None,
                            doi=(entry.get("doi") or "").strip() or None,
                            reason=reason or "核对失败",
                        )
                    )
                    continue
                key = norm_arxiv(cand.arxiv_id) if cand.arxiv_id else norm_title(cand.title)
                if key in seen:
                    continue
                seen.add(key)
                if len(references) < limit:
                    references.append(self._to_item(cand, style, entry.get("formatted")))
        return ReferencesResponse(
            query=query,
            style=style,
            references=references,
            dropped=dropped,
            sources_used=res.sources_used,
        )
