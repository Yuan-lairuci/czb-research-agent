"""文本切块：ParsedPaper 契约 dict → 页感知 chunks。

输入为模块一产出的 ParsedPaper 契约 dict，输出 chunks:
{"chunk_id","paper_id","page_no","text","section"?}。
块约 500 字符、重叠约 100 字符；heading 作为 section 上下文拼入块文本，
每块保留其所在页的 page_no（同一块绝不跨页）。
"""
from __future__ import annotations

from typing import Any

CHUNK_SIZE = 500
CHUNK_OVERLAP = 100

_BLOCK_TYPES = {"text", "heading", "table", "formula", "figure"}
_SENTENCE_END = "。！？；.!?:;"


class ChunkerContractError(ValueError):
    """输入 dict 不符合 ParsedPaper 契约。"""


def _validate(paper: dict[str, Any]) -> str:
    """校验 ParsedPaper 契约并返回 paper_id。"""
    if not isinstance(paper, dict):
        raise ChunkerContractError("paper 必须是 dict")
    paper_id = paper.get("paper_id")
    if not isinstance(paper_id, str) or not paper_id.strip():
        raise ChunkerContractError("paper_id 必须是非空字符串")
    pages = paper.get("pages")
    if not isinstance(pages, list):
        raise ChunkerContractError("pages 必须是列表")
    for page in pages:
        if not isinstance(page, dict):
            raise ChunkerContractError(f"page 条目必须是 dict: {page!r}")
        if not isinstance(page.get("page_no"), int):
            raise ChunkerContractError(f"page_no 必须是整数: {page!r}")
        blocks = page.get("blocks")
        if not isinstance(blocks, list):
            raise ChunkerContractError(f"page {page.get('page_no')} 的 blocks 必须是列表")
        for block in blocks:
            if not isinstance(block, dict):
                raise ChunkerContractError(f"block 条目必须是 dict: {block!r}")
            btype = block.get("type")
            if btype not in _BLOCK_TYPES:
                raise ChunkerContractError(f"block.type 必须属于 {sorted(_BLOCK_TYPES)}: {btype!r}")
            if not isinstance(block.get("text"), str):
                raise ChunkerContractError(f"block.text 必须是字符串: {block!r}")
            if btype == "heading" and not isinstance(block.get("level"), int):
                raise ChunkerContractError(f"heading 块缺少整数 level: {block!r}")
    return paper_id


def _page_pieces(
    blocks: list[dict[str, Any]], section: str | None
) -> tuple[list[tuple[str, str | None]], str | None]:
    """页内 blocks → (文本片段, 生效 section) 序列；section 可跨页延续。"""
    pieces: list[tuple[str, str | None]] = []
    for block in blocks:
        text = block["text"].strip()
        if block["type"] == "heading":
            section = text or section
        if text:
            pieces.append((text, section))
    return pieces, section


def _find_boundary(text: str, start: int, end: int) -> int | None:
    """在后半窗口内回退寻找段落/句子边界；返回切点（边界符之后）。"""
    low = start + (end - start) // 2
    for i in range(end - 1, low - 1, -1):
        if text[i] == "\n":
            return i + 1
    for i in range(end - 1, low - 1, -1):
        if text[i] in _SENTENCE_END:
            return i + 1
    return None


def _split_spans(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """切分文本为 [start, end) 片段：约 size 字符、相邻重叠约 overlap 字符。"""
    spans: list[tuple[int, int]] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            cut = _find_boundary(text, start, end)
            if cut is not None and cut > start:
                end = cut
        spans.append((start, end))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return spans


def _section_at(
    pieces: list[tuple[str, str | None]], starts: list[int], pos: int
) -> str | None:
    """位置 pos 处生效的 section（其所在/之前最近 heading）。"""
    section = None
    for (_, sec), st in zip(pieces, starts, strict=True):
        if st > pos:
            break
        section = sec
    return section


def chunk_paper(
    paper: dict[str, Any],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[dict[str, Any]]:
    """按契约切块：页感知、heading 作 section 上下文、块约 500 字符重叠约 100。"""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("要求 chunk_size > 0 且 0 <= overlap < chunk_size")
    paper_id = _validate(paper)
    chunks: list[dict[str, Any]] = []
    carried_section: str | None = None
    for page in paper["pages"]:
        page_no = page["page_no"]
        pieces, carried_section = _page_pieces(page["blocks"], carried_section)
        if not pieces:
            continue
        page_text = "\n".join(text for text, _ in pieces)
        starts: list[int] = []
        pos = 0
        for text, _ in pieces:
            starts.append(pos)
            pos += len(text) + 1
        for seq, (start, end) in enumerate(_split_spans(page_text, chunk_size, overlap)):
            raw = page_text[start:end]
            if not raw.strip():
                continue
            section = _section_at(pieces, starts, start)
            text = raw.strip()
            if section and not text.startswith(section):
                text = f"{section}\n{text}"
            chunk: dict[str, Any] = {
                "chunk_id": f"{paper_id}:p{page_no}:{seq}",
                "paper_id": paper_id,
                "page_no": page_no,
                "text": text,
            }
            if section:
                chunk["section"] = section
            chunks.append(chunk)
    return chunks
