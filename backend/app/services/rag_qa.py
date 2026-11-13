"""RAG 问答引擎：检索 → 带 [S#] 锚点提示词 → 生成 → 引文逐字后校验。

防幻觉核心：模型必须在 "---QUOTES---" 块中给出每处 [S#] 的逐字原文引文；
后校验将每条引文与其来源 chunk text 做严格子串匹配，失败则该来源 verified=False
且整体 verified=False（宁可降级，不可轻信）。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from app.models.qa_models import AskResponse, SourceOut
from app.services.chat_service import ChatService
from app.services.chunker import chunk_paper
from app.services.embedding import EmbeddingBackend, get_embedding_backend
from app.services.vector_store import NumpyVectorStore, VectorStore

QUOTES_MARKER = "---QUOTES---"
REFUSAL_TEXT = "资料中未找到"
_CITE_RE = re.compile(r"\[S(\d+)\]")
_QUOTE_RE = re.compile(r"^\[S(\d+)\]\s*(.+)$")


class RagError(RuntimeError):
    """RAG 问答流程错误。"""


class ParsedPaperNotFoundError(FileNotFoundError):
    """parsed/{paper_id}.json 不存在（模块一尚未产出）。"""


def default_parsed_dir() -> Path:
    """已解析论文目录：env QA_PARSED_DIR 优先，否则 <backend>/data/parsed。"""
    env = os.environ.get("QA_PARSED_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / "parsed"


def _build_prompt(
    question: str, chunks: list[dict[str, Any]], multi_paper: bool
) -> str:
    """按 [S1][S2] 编号构造提示词，强制锚点标注与逐字引文。"""
    lines = ["你是一个严格基于给定资料回答的学术问答助手。请根据以下资料回答问题。", "", "资料："]
    for i, chunk in enumerate(chunks, start=1):
        header = f"[S{i}] (paper_id={chunk['paper_id']}, page_no={chunk['page_no']})"
        lines.append(header)
        lines.append(chunk["text"])
        lines.append("")
    lines.extend(
        [
            "回答要求：",
            "1. 每个论断必须标注依据的来源编号，格式如 [S1]、[S2]；可以多处标注。",
            "2. 只能依据资料回答，禁止编造；若资料中没有充分依据，直接回答"
            f"\"{REFUSAL_TEXT}\"。",
            "3. 代码、公式、专有名词保留英文原样。",
            f"4. 回答正文结束后，单独一行输出 {QUOTES_MARKER}，然后为每个引用过的来源"
            "输出一行引文，格式：[S1] 后跟该来源中支持你论断的逐字原文（必须与资料完全一致，"
            "不得改写）。",
        ]
    )
    if multi_paper:
        lines.append("5. 涉及多篇论文时逐点对比，并分别标注每一点的来源。")
    lines.append("")
    lines.append(f"问题：{question}")
    return "\n".join(lines)


def _parse_answer(raw: str) -> tuple[str, dict[int, str]]:
    """拆分正文与 QUOTES 块 → (answer, {source_index: quote})。"""
    if QUOTES_MARKER in raw:
        answer, _, quotes_part = raw.partition(QUOTES_MARKER)
    else:
        answer, quotes_part = raw, ""
    quote_map: dict[int, str] = {}
    for line in quotes_part.strip().splitlines():
        m = _QUOTE_RE.match(line.strip())
        if m:
            quote_map[int(m.group(1))] = m.group(2).strip()
    return answer.strip(), quote_map


class RagQAEngine:
    """检索式问答引擎：chunk → embed → store；ask 检索 + 生成 + 后校验。"""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedder: EmbeddingBackend | None = None,
        chat: Any | None = None,
        parsed_dir: str | Path | None = None,
    ) -> None:
        self.store = store if store is not None else NumpyVectorStore()
        self.embedder = embedder if embedder is not None else get_embedding_backend()
        self.chat = chat if chat is not None else ChatService()
        self.parsed_dir = Path(parsed_dir) if parsed_dir else default_parsed_dir()

    def index_paper(self, paper_id: str) -> dict[str, int]:
        """读 parsed/{paper_id}.json → chunk → embed → 落库；返回 {num_chunks, num_pages}。"""
        path = self.parsed_dir / f"{paper_id}.json"
        if not path.is_file():
            raise ParsedPaperNotFoundError(f"未找到已解析论文: {path}")
        paper = json.loads(path.read_text(encoding="utf-8"))
        chunks = chunk_paper(paper)
        vectors = self.embedder.embed([c["text"] for c in chunks])
        self.store.remove_paper(paper_id)
        self.store.add(chunks, vectors)
        self.store.save()
        return {"num_chunks": len(chunks), "num_pages": len(paper.get("pages", []))}

    def status(self) -> dict[str, Any]:
        """已索引论文 → chunk 数。"""
        papers = self.store.papers()
        return {"papers": papers, "total_chunks": sum(papers.values())}

    async def ask(
        self,
        question: str,
        paper_ids: list[str] | None = None,
        top_k: int = 5,
    ) -> AskResponse:
        """检索 top_k → 生成带 [S#] 锚点回答 → 引文逐字后校验。"""
        question = question.strip()
        if not question:
            raise RagError("question 不能为空")
        if top_k <= 0:
            raise RagError("top_k 必须为正整数")
        hits = self.store.query(
            self.embedder.embed([question])[0], top_k=top_k, paper_ids=paper_ids
        )
        if not hits:
            return AskResponse(answer=REFUSAL_TEXT, sources=[], verified=True)

        chunks = [h["chunk"] for h in hits]
        multi = len({c["paper_id"] for c in chunks}) > 1
        prompt = _build_prompt(question, chunks, multi)
        raw = await self.chat.chat(
            [
                {
                    "role": "user",
                    "content": prompt,
                }
            ]
        )
        answer, quote_map = _parse_answer(raw or "")

        cited: list[int] = []
        for m in _CITE_RE.finditer(answer):
            idx = int(m.group(1))
            if idx not in cited:
                cited.append(idx)

        if not cited:
            verified = REFUSAL_TEXT in answer
            return AskResponse(answer=answer or REFUSAL_TEXT, sources=[], verified=verified)

        sources: list[SourceOut] = []
        all_ok = True
        for idx in cited:
            if 1 <= idx <= len(chunks):
                chunk = chunks[idx - 1]
                quote = quote_map.get(idx, "")
                ok = bool(quote) and quote in chunk["text"]
                if not ok:
                    all_ok = False
                sources.append(
                    SourceOut(
                        chunk_id=chunk["chunk_id"],
                        paper_id=chunk["paper_id"],
                        page_no=chunk["page_no"],
                        quote=quote,
                        score=float(hits[idx - 1]["score"]),
                        verified=ok,
                    )
                )
            else:
                all_ok = False
                sources.append(
                    SourceOut(
                        chunk_id=f"S{idx}",
                        paper_id="",
                        page_no=0,
                        quote=quote_map.get(idx, ""),
                        score=0.0,
                        verified=False,
                    )
                )
        return AskResponse(answer=answer, sources=sources, verified=all_ok)
