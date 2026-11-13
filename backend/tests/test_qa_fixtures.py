"""模块二测试公共 fixture：3 篇契约 dict、chat stub、引擎/应用装配辅助。

零网络：chat 一律用 stub（monkeypatch 或构造注入），embedding 用 HashEmbedding。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.api.qa import router as qa_router
from app.services.embedding import HashEmbedding
from app.services.rag_qa import RagQAEngine
from app.services.vector_store import NumpyVectorStore

# ---- 3 篇小契约 dict（ParsedPaper 同构） ----

ATTN_TITLE_SENTENCE = (
    "The dominant sequence transduction models are based on complex recurrent or "
    "convolutional neural networks that include an encoder and a decoder."
)
ATTN_ADAM_SENTENCE = (
    "We used the Adam optimizer with beta1 = 0.9, beta2 = 0.98 and epsilon = 1e-9."
)
ATTN_WMT_SENTENCE = (
    "We trained on the standard WMT 2014 English-German dataset consisting of "
    "about 4.5 million sentence pairs."
)
ATTN_LR_SENTENCE = (
    "We used lrate = d_model^-0.5 * min(step_num^-0.5, step_num * warmup_steps^-1.5)."
)

ATTN_PAPER: dict[str, Any] = {
    "paper_id": "attn-1706",
    "title": "Attention Is All You Need",
    "num_pages": 2,
    "pages": [
        {
            "page_no": 1,
            "blocks": [
                {"type": "heading", "text": "1 Introduction", "level": 1},
                {"type": "text", "text": ATTN_TITLE_SENTENCE},
                {
                    "type": "text",
                    "text": "Recurrent models typically factor computation along the symbol "
                    "positions of the input and output sequences.",
                },
            ],
        },
        {
            "page_no": 2,
            "blocks": [
                {"type": "heading", "text": "3 Model Architecture", "level": 1},
                {"type": "text", "text": "The Transformer follows an encoder-decoder structure."},
                {"type": "text", "text": ATTN_ADAM_SENTENCE},
                {"type": "formula", "text": ATTN_LR_SENTENCE},
            ],
        },
        {
            "page_no": 3,
            "blocks": [
                {"type": "heading", "text": "6 Training", "level": 1},
                {"type": "text", "text": ATTN_WMT_SENTENCE},
                {"type": "table", "text": "BLEU on EN-DE 28.4; EN-FR 41.8."},
            ],
        },
    ],
    "references": [
        {
            "index": 1,
            "raw": "Sutskever et al. Sequence to sequence learning.",
            "arxiv_id": "1409.3215",
        },
    ],
    "meta": {"sha256": "f" * 64, "source_filename": "1706.03762v1.pdf"},
}

BERT_PAPER: dict[str, Any] = {
    "paper_id": "bert-demo",
    "title": "BERT Demo",
    "num_pages": 1,
    "pages": [
        {
            "page_no": 1,
            "blocks": [
                {"type": "heading", "text": "Abstract", "level": 1},
                {
                    "type": "text",
                    "text": "BERT is a bidirectional Transformer pre-trained with masked "
                    "language modeling and next sentence prediction.",
                },
                {
                    "type": "text",
                    "text": "The base model has 110 million parameters and 12 layers.",
                },
            ],
        }
    ],
    "references": [],
    "meta": {"sha256": "a" * 64, "source_filename": "bert_demo.pdf"},
}

RAG_PAPER: dict[str, Any] = {
    "paper_id": "rag-demo",
    "title": "RAG Demo",
    "num_pages": 1,
    "pages": [
        {
            "page_no": 1,
            "blocks": [
                {"type": "heading", "text": "Method", "level": 1},
                {
                    "type": "text",
                    "text": "Retrieval augmented generation combines a parametric generator "
                    "with a non-parametric dense vector index of Wikipedia.",
                },
                {
                    "type": "text",
                    "text": "The retriever is a dense passage retriever trained end to end.",
                },
            ],
        }
    ],
    "references": [],
    "meta": {"sha256": "b" * 64, "source_filename": "rag_demo.pdf"},
}

ALL_PAPERS = {"attn-1706": ATTN_PAPER, "bert-demo": BERT_PAPER, "rag-demo": RAG_PAPER}


# ---- chat stub ----

class ScriptedChat:
    """按脚本返回回答的 chat stub；记录收到的 messages。"""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append(messages)
        return self.answer


class QuotingChat(ScriptedChat):
    """从提示词的资料块中选最长一行作为逐字引文；tamper 可注入篡改引文。"""

    def __init__(
        self,
        answer_head: str = "论文提出了一种新的序列转换架构",
        tamper: dict[int, str] | None = None,
        cite: list[int] | None = None,
    ) -> None:
        super().__init__("")
        self.answer_head = answer_head
        self.tamper = tamper or {}
        self.cite = cite

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append(messages)
        prompt = messages[0]["content"]
        blocks = _parse_prompt_sources(prompt)
        ids = self.cite if self.cite is not None else sorted(blocks)
        parts = [f"{self.answer_head}[S{i}]" for i in ids]
        quotes = []
        for i in ids:
            quote = self.tamper.get(i, max(blocks[i], key=len))
            quotes.append(f"[S{i}] {quote}")
        return "\n".join(parts) + "\n---QUOTES---\n" + "\n".join(quotes)


def _parse_prompt_sources(prompt: str) -> dict[int, list[str]]:
    """解析提示词里的 [Sn] 资料块 → {编号: [块内各行]}。"""
    blocks: dict[int, list[str]] = {}
    current: int | None = None
    started = False
    for line in prompt.splitlines():
        if line.startswith("[S") and "] (paper_id=" in line:
            current = int(line[2 : line.index("]")])
            blocks[current] = []
            started = True
        elif started and line.startswith("回答要求"):
            current = None
        elif current is not None and line.strip():
            blocks[current].append(line)
    return blocks


# ---- 装配辅助 ----

def write_parsed(parsed_dir: Path, paper: dict[str, Any]) -> Path:
    """把契约 dict 落盘为 parsed/{paper_id}.json。"""
    parsed_dir.mkdir(parents=True, exist_ok=True)
    path = parsed_dir / f"{paper['paper_id']}.json"
    path.write_text(json.dumps(paper, ensure_ascii=False), encoding="utf-8")
    return path


def make_engine(tmp_path: Path, chat: Any | None = None) -> RagQAEngine:
    """tmp_path 隔离的引擎：NumpyVectorStore + HashEmbedding + 可注入 chat。"""
    store = NumpyVectorStore(index_dir=tmp_path / "index")
    return RagQAEngine(
        store=store,
        embedder=HashEmbedding(),
        chat=chat if chat is not None else ScriptedChat("资料中未找到"),
        parsed_dir=tmp_path / "parsed",
    )


def build_app(engine: RagQAEngine) -> FastAPI:
    """独立 FastAPI app 挂载 /api/qa router（不依赖 main.py）。"""
    from app.api.qa import set_engine

    set_engine(engine)
    app = FastAPI()
    app.include_router(qa_router)
    return app
