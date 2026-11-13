"""语义去重与版本识别服务（模块一）。

判定规则：
- 标题归一化（去版本号/标点/小写）相似 + 正文 5-gram shingle Jaccard >= 0.6 判同一工作。
- 版本新旧：arXiv 版本号（v7 > v1）优先，其次文本日期，再次页数。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

SHINGLE_N = 5
JACCARD_THRESHOLD = 0.6
TITLE_SIM_THRESHOLD = 0.8

_ARXIV_VER_RE = re.compile(r"(\d{4}\.\d{4,5})v(\d+)", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
_MDY_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)
_DMY_RE = re.compile(
    r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+(\d{4})\b",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_title(title: str) -> str:
    """标题归一化：小写、去 arXiv 版本号与标点、压缩空白。"""
    lowered = (title or "").lower()
    lowered = re.sub(r"\bv\d+\b", " ", lowered)
    lowered = re.sub(r"[^a-z0-9\u4e00-\u9fff ]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def title_similarity(title_a: str, title_b: str) -> float:
    """归一化后标题 token 集合 Jaccard；完全包含视为 1.0。"""
    a = normalize_title(title_a)
    b = normalize_title(title_b)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def body_text_for_dedup(contract: dict[str, Any]) -> str:
    """正文文本（标题 + References 之前的 text/heading 块），供 shingle 使用。"""
    parts: list[str] = [str(contract.get("title", ""))]
    for page in contract.get("pages", []):
        for block in page.get("blocks", []):
            btype = block.get("type")
            text = str(block.get("text", ""))
            if btype == "heading" and re.match(
                r"^(?:references|bibliography)\b", text.strip(), re.IGNORECASE
            ):
                return " ".join(parts)
            if btype in ("text", "heading"):
                parts.append(text)
    return " ".join(parts)


def shingle_set(text: str, n: int = SHINGLE_N) -> set[int]:
    """正文 n-gram（默认 5）词 shingle 集合；哈希为 int 控内存。"""
    tokens = _TOKEN_RE.findall((text or "").lower())
    out: set[int] = set()
    for i in range(len(tokens) - n + 1):
        gram = " ".join(tokens[i : i + n]).encode("utf-8", "ignore")
        out.add(int.from_bytes(hashlib.blake2b(gram, digest_size=8).digest(), "big"))
    return out


def jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def arxiv_version(contract: dict[str, Any]) -> tuple[str | None, int]:
    """扫描文件名/标题/首页文本中的 arXiv 标识，返回 (arxiv_id, 版本号)。"""
    haystacks = [
        str((contract.get("meta") or {}).get("source_filename", "")),
        str(contract.get("title", "")),
    ]
    for page in contract.get("pages", [])[:2]:
        for block in page.get("blocks", []):
            haystacks.append(str(block.get("text", "")))
    for hay in haystacks:
        m = _ARXIV_VER_RE.search(hay)
        if m:
            return m.group(1), int(m.group(2))
    return None, 0


def text_date(contract: dict[str, Any]) -> tuple[int, int, int]:
    """首页文本中的日期（支持 ISO 与英文月名），按 (y, m, d) 返回；缺失为全 0。"""
    texts: list[str] = []
    for page in contract.get("pages", [])[:2]:
        for block in page.get("blocks", []):
            texts.append(str(block.get("text", "")))
    blob = "\n".join(texts)
    m = _ISO_DATE_RE.search(blob)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _MDY_RE.search(blob)
    if m:
        return (int(m.group(3)), _MONTHS.get(m.group(1).lower()[:3], 0), int(m.group(2)))
    m = _DMY_RE.search(blob)
    if m:
        return (int(m.group(3)), _MONTHS.get(m.group(2).lower()[:3], 0), int(m.group(1)))
    return (0, 0, 0)


def compare_contracts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """两篇契约比较：标题相似 + 正文 5-gram Jaccard，达标判同工作。"""
    t_sim = title_similarity(str(a.get("title", "")), str(b.get("title", "")))
    jac = jaccard(shingle_set(body_text_for_dedup(a)), shingle_set(body_text_for_dedup(b)))
    same_work = t_sim >= TITLE_SIM_THRESHOLD and jac >= JACCARD_THRESHOLD
    return {
        "paper_id_a": a.get("paper_id"),
        "paper_id_b": b.get("paper_id"),
        "title_similarity": round(t_sim, 3),
        "jaccard": round(jac, 3),
        "same_work": same_work,
    }


def suggest_keep(contracts: list[dict[str, Any]]) -> tuple[str, str]:
    """组内建议保留：arXiv 版本号 > 文本日期 > 页数。返回 (paper_id, 理由)。"""

    def rank(c: dict[str, Any]) -> tuple[int, tuple[int, int, int], int]:
        _, ver = arxiv_version(c)
        return (ver, text_date(c), int(c.get("num_pages", 0)))

    ranked = sorted(contracts, key=rank)
    best = ranked[-1]
    keep_id = str(best.get("paper_id"))
    losers = ranked[:-1]
    reasons: list[str] = []
    for other in losers:
        _, ver_b = arxiv_version(best)
        _, ver_o = arxiv_version(other)
        if ver_b != ver_o and ver_b > 0:
            reasons.append(f"arXiv 版本 v{ver_b} > v{ver_o}")
        elif text_date(best) != text_date(other) and text_date(best) > (0, 0, 0):
            reasons.append(f"文本日期更新 {text_date(best)} > {text_date(other)}")
        else:
            reasons.append(f"页数更多 {best.get('num_pages')} > {other.get('num_pages')}")
    return keep_id, "；".join(reasons) or "无可比依据，保留列表末位"


def scan_duplicates(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """并查集成组，返回契约要求的去重组列表（仅含 >=2 篇的组）。"""
    ids = [str(c.get("paper_id")) for c in contracts]
    parent = {pid: pid for pid in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(contracts)):
        for j in range(i + 1, len(contracts)):
            if find(ids[i]) == find(ids[j]):
                continue
            if compare_contracts(contracts[i], contracts[j])["same_work"]:
                parent[find(ids[i])] = find(ids[j])

    buckets: dict[str, list[dict[str, Any]]] = {}
    for c, pid in zip(contracts, ids, strict=True):
        buckets.setdefault(find(pid), []).append(c)

    groups: list[dict[str, Any]] = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        keep_id, keep_reason = suggest_keep(members)
        pair = compare_contracts(members[0], members[1])
        groups.append(
            {
                "paper_ids": sorted(str(m.get("paper_id")) for m in members),
                "same_work": True,
                "suggested_keep": keep_id,
                "reason": (
                    f"标题相似度 {pair['title_similarity']}、正文 {SHINGLE_N}-gram Jaccard "
                    f"{pair['jaccard']} >= {JACCARD_THRESHOLD} 判同一工作；建议保留 "
                    f"{keep_id}：{keep_reason}"
                ),
            }
        )
    groups.sort(key=lambda g: g["paper_ids"][0])
    return groups
