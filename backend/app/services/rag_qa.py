"""RAG 问答引擎：检索 → 带 [S#] 锚点提示词 → 生成 → 引文逐字后校验。

防幻觉核心：模型必须在 "---QUOTES---" 块中给出每处 [S#] 的逐字原文引文；
后校验将每条引文与其来源 chunk text 做严格子串匹配，失败则该来源 verified=False
且整体 verified=False（宁可降级，不可轻信）。
"""
from __future__ import annotations

import json
import os
import re
import threading
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
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_REWRITE_PROMPT = (
    "你是学术检索助手。把下面的研究问题改写成一条英文检索查询，"
    "用于在英文学术论文库中做向量检索。保留模型名、方法名等专有名词原文；"
    "只输出一行英文查询，不超过 12 个词，不要任何解释。\n\n问题：{q}"
)


def _clean_rewrite(raw: str) -> str | None:
    """改写结果清洗：取首行、去引号；无英文词或夹带引文标记则弃用。"""
    text = (raw or "").strip()
    if not text:
        return None
    line = text.splitlines()[0].strip().strip("\"'“”‘’ ")
    if QUOTES_MARKER in line or not re.search(r"[A-Za-z]{2,}", line):
        return None
    return line[:200]


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


def _prompt_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """提示词历史剔除拒答轮：连续"未找到"会传染给模型诱发更多拒答；
    拒答轮的问题仍参与检索（_retrieve 用完整历史做指代桥接）。"""
    return [t for t in history if t.get("answer") and REFUSAL_TEXT not in t["answer"]]


def _build_prompt(
    question: str,
    chunks: list[dict[str, Any]],
    multi_paper: bool,
    history: list[dict[str, str]] | None = None,
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
            "2. 只能依据资料回答，禁止编造；资料中只要有问题相关的内容，就必须作答，"
            "可以归纳、概括、并列多点，不必拘泥于逐字对应；仅当资料与问题完全无关、"
            f"没有任何可用信息时才回答\"{REFUSAL_TEXT}\"。",
            "3. 代码、公式、专有名词保留英文原样。",
            f"4. 回答正文结束后，单独一行输出 {QUOTES_MARKER}，然后为每个引用过的来源"
            "输出一行引文，格式：[S1] 后跟该来源中支持你论断的逐字原文（必须与资料完全一致，"
            "不得改写）。",
        ]
    )
    if multi_paper:
        lines.append("5. 涉及多篇论文时逐点对比，并分别标注每一点的来源。")
    if history:
        import re as _re

        lines.append("对话历史（用于理解指代与追问，不要重复回答）：")
        for t in history:
            clean = _re.sub(r"\[\d+\]", "", t["answer"]).strip()  # 去掉旧编号，避免与本轮资料编号冲突
            lines.append(f"问：{t['question']}")
            lines.append(f"答：{clean}")
        lines.append("")
        lines.append(
            "注意：最后一个问题可能是对对话历史的追问，其中的“它/其/该/这个”等指代"
            "应先依据对话历史还原为具体对象（如某模型、某论文），然后在资料中查找该对象的"
            "相关事实作答；只要资料中有相关内容，必须作答，不要因问题简短而拒答。"
        )
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


def _verify_sources(
    answer: str,
    quote_map: dict[int, str],
    chunks: list[dict[str, Any]],
    hits: list[dict[str, Any]],
) -> tuple[list[SourceOut], bool]:
    """引文逐字后校验：answer 里的 [S#] → 来源条目；quote 与 chunk text 严格子串匹配。"""
    cited: list[int] = []
    for m in _CITE_RE.finditer(answer):
        idx = int(m.group(1))
        if idx not in cited:
            cited.append(idx)

    if not cited:
        return [], (REFUSAL_TEXT in answer)

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
    return sources, all_ok


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
        self._index_lock = threading.Lock()

    def index_paper(self, paper_id: str) -> dict[str, int]:
        """读 parsed/{paper_id}.json → chunk → embed → 落库；返回 {num_chunks, num_pages}。"""
        with self._index_lock:
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

    def _retrieve(
        self,
        question: str,
        top_k: int,
        history: list[dict[str, str]],
        paper_ids: list[str] | None,
        extra_queries: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """多轮检索：本问 + 「上一问+本问」组合查询各检一路再合并——
        组合查询补上指代实体("它"→Llama 2)，加宽与不截断保证追问命中不被上一轮高分块挤掉。
        """
        embed_texts = [question]
        if extra_queries:
            embed_texts.extend(extra_queries)
        if history:
            embed_texts.append(history[-1]["question"] + " " + question)
        vecs = self.embedder.embed(embed_texts)
        # 检索面始终加宽一倍：间接提问（问名字/结论）的目标块常排在 top_k 名开外
        width = top_k * 2
        merged: dict[str, dict[str, Any]] = {}
        for v in vecs:
            for h in self.store.query(v, top_k=width, paper_ids=paper_ids):
                cid = h["chunk"]["chunk_id"]
                if cid not in merged or h["score"] > merged[cid]["score"]:
                    merged[cid] = h
        hits = sorted(merged.values(), key=lambda h: h["score"], reverse=True)
        return [h["chunk"] for h in hits], hits

    async def _rewrite_query(
        self, question: str, history: list[dict[str, str]]
    ) -> str | None:
        """中文问题 → LLM 改写英文检索词（跨语言补齐）；非中文不动，失败静默降级。"""
        if not _CJK_RE.search(question):
            return None
        q = question
        if history:
            q = "上一问：" + history[-1]["question"] + "\n本问：" + question
        try:
            raw = await self.chat.chat(
                [{"role": "user", "content": _REWRITE_PROMPT.format(q=q)}],
                temperature=0,
                max_tokens=64,
            )
        except Exception:
            return None
        return _clean_rewrite(raw or "")

    async def _retrieve_smart(
        self,
        question: str,
        top_k: int,
        history: list[dict[str, str]],
        paper_ids: list[str] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """两段式检索：先用原问检索（零命中直接空返回，不浪费 LLM 调用）；
        中文问题再改写英文检索词补一路，两路命中并集按 chunk 取高分。"""
        chunks, hits = self._retrieve(question, top_k, history, paper_ids)
        if not hits:
            return chunks, hits
        en = await self._rewrite_query(question, history)
        if not en:
            return chunks, hits
        _, hits2 = self._retrieve(
            question, top_k, history, paper_ids, extra_queries=[en]
        )
        merged: dict[str, dict[str, Any]] = {h["chunk"]["chunk_id"]: h for h in hits}
        for h in hits2:
            cid = h["chunk"]["chunk_id"]
            if cid not in merged or h["score"] > merged[cid]["score"]:
                merged[cid] = h
        hits = sorted(merged.values(), key=lambda h: h["score"], reverse=True)
        return [h["chunk"] for h in hits], hits

    async def ask(
        self,
        question: str,
        paper_ids: list[str] | None = None,
        top_k: int = 5,
        history: list[dict[str, str]] | None = None,
    ) -> AskResponse:
        """检索 top_k → 生成带 [S#] 锚点回答 → 引文逐字后校验。"""
        question = question.strip()
        if not question:
            raise RagError("question 不能为空")
        if top_k <= 0:
            raise RagError("top_k 必须为正整数")
        history = history or []
        chunks, hits = await self._retrieve_smart(question, top_k, history, paper_ids)
        if not hits:
            return AskResponse(answer=REFUSAL_TEXT, sources=[], verified=True)
        multi = len({c["paper_id"] for c in chunks}) > 1
        prompt = _build_prompt(question, chunks, multi, _prompt_history(history))
        raw = await self.chat.chat(
            [
                {
                    "role": "user",
                    "content": prompt,
                }
            ]
        )
        answer, quote_map = _parse_answer(raw or "")
        sources, verified = _verify_sources(answer, quote_map, chunks, hits)
        return AskResponse(answer=answer or REFUSAL_TEXT, sources=sources, verified=verified)

    async def ask_stream(
        self,
        question: str,
        paper_ids: list[str] | None = None,
        top_k: int = 5,
        history: list[dict[str, str]] | None = None,
    ):
        """流式问答：检索 → 逐段产出正文增量（吞掉 QUOTES 引文块）→ 收尾产出校验结果。

        事件契约：{"type":"delta","delta":str} 逐段正文；
        {"type":"done","answer":str,"sources":list,"verified":bool} 收尾全量结果。
        """
        question = question.strip()
        if not question:
            raise RagError("question 不能为空")
        if top_k <= 0:
            raise RagError("top_k 必须为正整数")
        history = history or []
        chunks, hits = await self._retrieve_smart(question, top_k, history, paper_ids)
        if not hits:
            yield {"type": "done", "answer": REFUSAL_TEXT, "sources": [], "verified": True}
            return
        multi = len({c["paper_id"] for c in chunks}) > 1
        prompt = _build_prompt(question, chunks, multi, _prompt_history(history))

        raw_parts: list[str] = []
        pending = ""  # 尾部暂存：防止把 QUOTES_MARKER 半截前缀先发给用户
        quotes_seen = False
        async for piece in self.chat.chat_stream([{"role": "user", "content": prompt}]):
            raw_parts.append(piece)
            if quotes_seen:
                continue
            pending += piece
            if QUOTES_MARKER in pending:
                pre, _, _ = pending.partition(QUOTES_MARKER)
                if pre:
                    yield {"type": "delta", "delta": pre}
                pending = ""
                quotes_seen = True
                continue
            keep = len(QUOTES_MARKER) - 1
            if len(pending) > keep:
                emit = pending[:-keep] if keep else pending
                pending = pending[len(emit) :]
                yield {"type": "delta", "delta": emit}
        if not quotes_seen and pending:
            yield {"type": "delta", "delta": pending}
        answer, quote_map = _parse_answer("".join(raw_parts))
        sources, verified = _verify_sources(answer, quote_map, chunks, hits)
        yield {
            "type": "done",
            "answer": answer or REFUSAL_TEXT,
            "sources": sources,
            "verified": verified,
        }
