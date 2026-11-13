"""chunker 契约、页码保留、检索排序、paper_ids 过滤测试。"""
from __future__ import annotations

import pytest

from app.services.chunker import ChunkerContractError, chunk_paper
from app.services.embedding import HashEmbedding
from app.services.vector_store import NumpyVectorStore
from tests.test_qa_fixtures import ATTN_PAPER, BERT_PAPER, RAG_PAPER


def test_chunker_contract_shape():
    """chunks 字段符合契约：chunk_id/paper_id/page_no/text，section 可选。"""
    chunks = chunk_paper(ATTN_PAPER)
    assert chunks
    for c in chunks:
        assert set(c) >= {"chunk_id", "paper_id", "page_no", "text"}
        assert isinstance(c["page_no"], int)
        assert c["paper_id"] == "attn-1706"
        assert c["text"].strip()
    ids = [c["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids))


def test_chunker_page_no_preserved_and_no_cross_page():
    """每块保留所在 page_no，且文本不跨页拼接。"""
    chunks = chunk_paper(ATTN_PAPER)
    by_page = {}
    for c in chunks:
        by_page.setdefault(c["page_no"], []).append(c)
    assert set(by_page) == {1, 2, 3}
    page1_text = " ".join(c["text"] for c in by_page[1])
    assert "Recurrent models" in page1_text
    assert "Adam optimizer" not in page1_text
    page2_text = " ".join(c["text"] for c in by_page[2])
    assert "Adam optimizer" in page2_text
    assert "WMT 2014" not in page2_text


def test_chunker_section_context_and_overlap():
    """heading 作 section 上下文拼入；长文切块约 500 字符、相邻重叠约 100。"""
    chunks = chunk_paper(ATTN_PAPER)
    for c in chunks:
        if c.get("section"):
            assert c["text"].startswith(c["section"])
    assert all(c.get("section") for c in chunks)

    long_text = " ".join(
        f"这是第{i}句测试句子，用于验证切块的块大小与相邻重叠行为。" for i in range(80)
    )
    long_paper = {
        "paper_id": "long-demo",
        "title": "Long Demo",
        "num_pages": 1,
        "pages": [
            {
                "page_no": 1,
                "blocks": [
                    {"type": "heading", "text": "3 实验", "level": 1},
                    {"type": "text", "text": long_text},
                ],
            }
        ],
        "references": [],
        "meta": {"sha256": "c" * 64, "source_filename": "long.pdf"},
    }
    long_chunks = chunk_paper(long_paper)
    assert len(long_chunks) >= 2
    for c in long_chunks:
        assert c["text"].startswith("3 实验")
        assert len(c["text"]) <= 700  # 500 目标 + section 前缀 + 边界回退余量
    assert all(len(c["text"]) >= 400 for c in long_chunks[:-1])
    for prev, nxt in zip(long_chunks, long_chunks[1:], strict=False):
        assert prev["text"][-50:] in nxt["text"]  # 相邻块共享约 100 字符重叠


def test_chunker_rejects_bad_contract():
    """契约校验失败抛 ChunkerContractError。"""
    bad = dict(ATTN_PAPER)
    bad["pages"] = [{"page_no": "x", "blocks": []}]
    with pytest.raises(ChunkerContractError):
        chunk_paper(bad)
    with pytest.raises(ChunkerContractError):
        chunk_paper({"paper_id": "", "pages": []})


def test_vector_store_ranking():
    """向量检索按余弦相似度降序，命中最相关的块。"""
    store = NumpyVectorStore()
    emb = HashEmbedding()
    papers = [ATTN_PAPER, BERT_PAPER, RAG_PAPER]
    chunks = [c for p in papers for c in chunk_paper(p)]
    store.add(chunks, emb.embed([c["text"] for c in chunks]))
    q = emb.embed(["The Adam optimizer beta1 = 0.9, beta2 = 0.98 and epsilon = 1e-9."])[0]
    hits = store.query(q, top_k=3)
    assert hits
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert hits[0]["chunk"]["paper_id"] == "attn-1706"
    assert "Adam optimizer" in hits[0]["chunk"]["text"]


def test_vector_store_paper_filter():
    """paper_ids 过滤只返回指定论文的块（单篇细粒度 / 多篇并集）。"""
    store = NumpyVectorStore()
    emb = HashEmbedding()
    chunks = [c for p in (ATTN_PAPER, BERT_PAPER, RAG_PAPER) for c in chunk_paper(p)]
    store.add(chunks, emb.embed([c["text"] for c in chunks]))
    q = emb.embed(["Transformer encoder decoder attention"])[0]
    single = store.query(q, top_k=10, paper_ids=["bert-demo"])
    assert single
    assert {h["chunk"]["paper_id"] for h in single} == {"bert-demo"}
    multi = store.query(q, top_k=10, paper_ids=["attn-1706", "rag-demo"])
    assert multi
    assert {h["chunk"]["paper_id"] for h in multi} <= {"attn-1706", "rag-demo"}
    none = store.query(q, top_k=5, paper_ids=["ghost-paper"])
    assert none == []
