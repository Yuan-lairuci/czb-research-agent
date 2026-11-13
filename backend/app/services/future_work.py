"""未来工作挖掘：Future Work/Conclusion/limitations 节提取 + 三态判定 + 时间线。

- 从各论文 blocks 的相关标题节提取未竟方向（句子级）；
- 用语料内年份更晚论文的正文对比判定状态：
  solved（关键词命中率 ≥ 0.6）/ partially（≥ 0.3）/ unresolved（< 0.3 或无后续论文）；
- 按论文年份升序排时间线。零网络，LLM 不参与。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from app.models.graph_models import CitationGraph, FutureWorkItem, FutureWorkResponse

_SECTION_RE = re.compile(r"future|conclusion|limitation|discussion|outlook", re.IGNORECASE)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")
_WORD_RE = re.compile(r"[a-z][a-z0-9]{3,}")
_MAX_ITEMS_PER_PAPER = 5
_KEYWORD_CAP = 6

STOPWORDS = {
    "about", "above", "after", "again", "against", "also", "among", "another",
    "because", "been", "before", "being", "below", "between", "both", "could",
    "does", "each", "even", "every", "first", "from", "further", "have",
    "having", "here", "however", "into", "itself", "just", "like", "make",
    "many", "more", "most", "must", "next", "only", "other", "over", "rather",
    "same", "seem", "should", "since", "some", "such", "take", "than", "that",
    "their", "theirs", "them", "then", "there", "these", "they", "this",
    "those", "through", "thus", "under", "until", "very", "want", "well",
    "were", "what", "when", "where", "which", "while", "will", "with",
    "within", "without", "would", "your", "ours", "paper", "papers", "propose",
    "proposed", "future", "work", "limitation", "limitations", "leave", "left",
    "remain", "remains", "remaining", "open", "interesting", "direction",
    "directions", "challenge", "challenges", "study", "studies", "explore",
    "exploring", "extend", "extending", "improve", "improving", "improved",
    "better", "section", "conclusion", "conclusions", "conclude", "discuss",
    "discussion", "results", "result", "method", "methods", "approach",
    "approaches", "model", "models", "task", "tasks", "data", "datasets",
    "dataset", "performance", "accuracy", "scale", "scaling", "large", "using",
    "use", "used", "based", "shown", "show", "shows", "present", "presents",
    "promising", "avenue", "avenues", "potential", "apply", "applied",
    "application", "applications", "generalize", "generalization", "investigate", "investigating",
    "understand", "understanding", "need",
    "needs", "needed", "allow", "allows", "enable", "enables", "provide",
    "provides", "support", "supports", "still", "currently", "recent",
    "recently", "existing", "multiple", "various", "several", "across",
    "different", "difficult", "difficulty", "deserves", "worth",
    "exploration", "hope", "expect", "expected", "question", "questions", "issue", "issues",
}

Status = Literal["solved", "unresolved", "partially"]


class FutureWorkError(ValueError):
    """未来工作挖掘失败。"""


def extract_section_texts(contract: dict[str, Any]) -> list[str]:
    """抽取 Future Work/Conclusion/limitations 等节的文本（每节一段）。"""
    sections: list[str] = []
    pages = contract.get("pages", [])
    flat: list[dict[str, Any]] = [
        b for p in pages for b in p.get("blocks", []) if b.get("type") in ("text", "heading")
    ]
    i = 0
    while i < len(flat):
        block = flat[i]
        if block.get("type") == "heading" and _SECTION_RE.search(block.get("text", "")):
            level = block.get("level", 1)
            parts: list[str] = []
            j = i + 1
            while j < len(flat):
                nxt = flat[j]
                if nxt.get("type") == "heading" and (nxt.get("level", 1) or 1) <= level:
                    break
                if nxt.get("type") == "text":
                    parts.append(nxt.get("text", ""))
                j += 1
            text = " ".join(parts).strip()
            if text:
                sections.append(text)
            i = j
        else:
            i += 1
    return sections


def split_items(section_text: str) -> list[str]:
    """节文本 → 未竟方向条目（句子级，过滤过短/过长）。"""
    items: list[str] = []
    for sentence in _SENT_SPLIT_RE.split(section_text):
        s = sentence.strip()
        if 15 <= len(s) <= 400:
            items.append(s)
    return items[:_MAX_ITEMS_PER_PAPER]


def keywords_of(text: str) -> list[str]:
    """内容关键词（len≥4、去停用词、去重、截断）。"""
    seen: set[str] = set()
    out: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        if word in STOPWORDS or word in seen:
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= _KEYWORD_CAP:
            break
    return out


def _corpus_body(contract: dict[str, Any]) -> str:
    parts = [
        b.get("text", "")
        for p in contract.get("pages", [])
        for b in p.get("blocks", [])
        if b.get("type") in ("text", "heading")
    ]
    return " ".join(parts).lower()


def judge_status(
    item: str,
    year: int | None,
    corpus: list[tuple[dict[str, Any], int | None]],
) -> tuple[Status, str]:
    """用年份更晚论文正文对比判定三态；返回 (status, evidence)。"""
    kws = keywords_of(item)
    if not kws:
        return "unresolved", "未能从条目提取关键词，判定为未解决"
    later = [
        (c, y) for c, y in corpus
        if y is not None and (year is None or y > year)
    ]
    if not later:
        return "unresolved", "语料内无年份更晚的论文可对比，判定为未解决"
    later.sort(key=lambda cy: cy[1] or 0)
    body_titles: list[str] = []
    bodies = []
    for c, y in later:
        bodies.append(_corpus_body(c))
        body_titles.append(f"《{c.get('title', '?')}》({y})")
    combined = " ".join(bodies)
    matched = [kw for kw in kws if kw in combined]
    ratio = len(matched) / len(kws)
    if ratio >= 0.6:
        status: Status = "solved"
        verdict = "判定为已解决"
    elif ratio >= 0.3:
        status = "partially"
        verdict = "判定为部分解决"
    else:
        status = "unresolved"
        verdict = "判定为未解决"
    evidence = (
        f"后续论文{'、'.join(body_titles[:3])} 正文命中关键词 "
        f"{len(matched)}/{len(kws)}（{', '.join(matched) or '无'}），{verdict}"
    )
    return status, evidence


def extract_future_work(
    graph: CitationGraph,
    contracts: dict[str, dict[str, Any]],
) -> FutureWorkResponse:
    """图谱语料 → 未来工作时间线（按年份升序，同年按 paper_id）。"""
    years = {
        n.paper_id: n.year for n in graph.nodes
    }
    corpus = [
        (contracts[n.paper_id], years[n.paper_id])
        for n in graph.nodes
        if n.paper_id in contracts
    ]

    items: list[FutureWorkItem] = []
    for node in sorted(
        graph.nodes, key=lambda n: (n.year if n.year is not None else 9999, n.paper_id)
    ):
        contract = contracts.get(node.paper_id)
        if contract is None:
            continue
        for section in extract_section_texts(contract):
            for sentence in split_items(section):
                status, evidence = judge_status(sentence, node.year, corpus)
                items.append(
                    FutureWorkItem(
                        item=sentence,
                        proposed_by=node.title,
                        paper_id=node.paper_id,
                        year=node.year,
                        status=status,
                        evidence=evidence,
                    )
                )
    items.sort(key=lambda it: (it.year if it.year is not None else 9999, it.paper_id))
    return FutureWorkResponse(graph_id=graph.graph_id, timeline=items)
