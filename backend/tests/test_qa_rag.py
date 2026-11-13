"""RAG 问答端点测试：溯源、跨文对比、拒答、降级、生命周期、错误处理。"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.services.chat_service import ChatAPIError, ChatNetworkError, ChatTimeoutError
from app.services.chunker import chunk_paper
from tests.test_qa_fixtures import (
    ALL_PAPERS,
    BERT_PAPER,
    RAG_PAPER,
    QuotingChat,
    ScriptedChat,
    build_app,
    make_engine,
    write_parsed,
)


def _indexed_client(tmp_path: Path, chat):
    """建好 3 篇索引并返回 TestClient。"""
    engine = make_engine(tmp_path, chat=chat)
    for paper in ALL_PAPERS.values():
        write_parsed(tmp_path / "parsed", paper)
        engine.index_paper(paper["paper_id"])
    return TestClient(build_app(engine))


def _chunk_text(source: dict) -> str:
    """按 chunk_id 取回契约切块原文。"""
    for c in chunk_paper(ALL_PAPERS[source["paper_id"]]):
        if c["chunk_id"] == source["chunk_id"]:
            return c["text"]
    raise AssertionError(f"chunk {source['chunk_id']} 不在 {source['paper_id']} 契约中")


def test_ask_single_paper_with_source_tracing(tmp_path):
    """单篇问答：answer 含 [S#]，sources 带 page_no+quote 且 quote 与 chunk verbatim 匹配。"""
    client = _indexed_client(tmp_path, QuotingChat())
    resp = client.post(
        "/api/qa/ask",
        json={"question": "Adam 优化器的超参数是什么?", "paper_ids": ["attn-1706"], "top_k": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["verified"] is True
    assert "[S" in data["answer"]
    assert data["sources"]
    for s in data["sources"]:
        assert s["paper_id"] == "attn-1706"
        assert s["page_no"] >= 1
        assert s["verified"] is True
        assert s["quote"] in _chunk_text(s)
    quotes = " ".join(s["quote"] for s in data["sources"])
    assert "Adam" in quotes or "sequence transduction" in quotes


def test_ask_cross_paper_comparison(tmp_path):
    """跨文对比：多篇 paper_ids 时 sources 覆盖多篇且都可溯源。"""
    chat = QuotingChat(answer_head="两篇论文的区别如下: ")
    client = _indexed_client(tmp_path, chat)
    resp = client.post(
        "/api/qa/ask",
        json={
            "question": "BERT 和 RAG 的建模方式有何不同?",
            "paper_ids": ["bert-demo", "rag-demo"],
            "top_k": 6,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["verified"] is True
    papers = {s["paper_id"] for s in data["sources"]}
    assert papers == {"bert-demo", "rag-demo"}
    for s in data["sources"]:
        assert s["verified"] is True
        assert s["quote"] in _chunk_text(s)


def test_refusal_when_no_evidence(tmp_path):
    """无依据拒答：答案为"资料中未找到"，零编造、零来源。"""
    client = _indexed_client(tmp_path, ScriptedChat("资料中未找到"))
    resp = client.post(
        "/api/qa/ask",
        json={"question": "这篇论文的作者养了几只猫?", "paper_ids": ["attn-1706"], "top_k": 3},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "资料中未找到"
    assert data["sources"] == []
    assert data["verified"] is True


def test_refusal_when_no_chunks_retrieved(tmp_path):
    """paper_ids 无命中时直接拒答，不调用生成。"""
    chat = ScriptedChat("我不该被调用")
    client = _indexed_client(tmp_path, chat)
    resp = client.post(
        "/api/qa/ask",
        json={"question": "任意问题", "paper_ids": ["ghost-paper"], "top_k": 3},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "资料中未找到"
    assert data["sources"] == []
    assert data["verified"] is True
    assert chat.calls == []


def test_quote_tamper_downgrade(tmp_path):
    """注入篡改引文 → 对应来源 verified=false 且整体 verified=false。"""
    tamper = {1: "这句引文是被篡改的，原文里没有这句话。"}
    chat = QuotingChat(answer_head="根据资料 [S1]", cite=[1], tamper=tamper)
    client = _indexed_client(tmp_path, chat)
    resp = client.post(
        "/api/qa/ask",
        json={"question": "论文的核心观点?", "paper_ids": ["attn-1706"], "top_k": 3},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["verified"] is False
    bad = [s for s in data["sources"] if not s["verified"]]
    assert bad and bad[0]["quote"] == tamper[1]
    assert "被篡改" in bad[0]["quote"]


def test_unverifiable_answer_without_quotes(tmp_path):
    """回答不含任何 [S#] 锚点且非拒答 → verified=false（无法溯源）。"""
    client = _indexed_client(tmp_path, ScriptedChat("我认为这篇论文非常有趣。"))
    resp = client.post(
        "/api/qa/ask",
        json={"question": "论文讲了什么?", "paper_ids": ["attn-1706"], "top_k": 3},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["verified"] is False
    assert data["sources"] == []


def test_index_status_lifecycle(tmp_path):
    """index → status 可见；重复 index 替换旧块不重复计数。"""
    engine = make_engine(tmp_path, chat=ScriptedChat("资料中未找到"))
    write_parsed(engine.parsed_dir, BERT_PAPER)
    client = TestClient(build_app(engine))
    assert client.get("/api/qa/status").json() == {"papers": {}, "total_chunks": 0}
    r1 = client.post("/api/qa/index", json={"paper_id": "bert-demo"})
    assert r1.status_code == 200
    assert r1.json()["paper_id"] == "bert-demo"
    assert r1.json()["num_chunks"] >= 1
    st = client.get("/api/qa/status").json()
    assert st["papers"] == {"bert-demo": r1.json()["num_chunks"]}
    assert st["total_chunks"] == r1.json()["num_chunks"]
    r2 = client.post("/api/qa/index", json={"paper_id": "bert-demo"})
    assert r2.status_code == 200
    st2 = client.get("/api/qa/status").json()
    assert st2["papers"] == {"bert-demo": r2.json()["num_chunks"]}
    assert st2["total_chunks"] == r2.json()["num_chunks"]


def test_index_status_lifecycle_two_papers(tmp_path):
    """多篇 index 后 status 聚合正确。"""
    engine = make_engine(tmp_path, chat=ScriptedChat("资料中未找到"))
    for p in (BERT_PAPER, RAG_PAPER):
        write_parsed(engine.parsed_dir, p)
        engine.index_paper(p["paper_id"])
    st = engine.status()
    assert set(st["papers"]) == {"bert-demo", "rag-demo"}
    assert st["total_chunks"] == sum(st["papers"].values())


def test_error_index_missing_papers(tmp_path):
    """parsed 文件缺失 → 404。"""
    engine = make_engine(tmp_path)
    client = TestClient(build_app(engine))
    resp = client.post("/api/qa/index", json={"paper_id": "nope"})
    assert resp.status_code == 404
    assert "未找到" in resp.json()["detail"]


def test_error_index_bad_contract(tmp_path):
    """契约非法的 parsed 文件 → 422。"""
    engine = make_engine(tmp_path)
    engine.parsed_dir.mkdir(parents=True, exist_ok=True)
    (engine.parsed_dir / "bad.json").write_text(
        '{"paper_id": "bad", "pages": [{"page_no": 1, "blocks": [{"type": "wat", "text": "x"}]}]}',
        encoding="utf-8",
    )
    client = TestClient(build_app(engine))
    resp = client.post("/api/qa/index", json={"paper_id": "bad"})
    assert resp.status_code == 422


def test_error_ask_empty_question(tmp_path):
    """空问题 → 422（pydantic 校验）。"""
    client = _indexed_client(tmp_path, ScriptedChat("x"))
    resp = client.post("/api/qa/ask", json={"question": ""})
    assert resp.status_code == 422


def test_error_chat_service_failure(tmp_path):
    """生成服务异常 → 502，不吞错误。"""
    for exc in (ChatTimeoutError("t"), ChatNetworkError("n"), ChatAPIError("a")):

        class Boom(ScriptedChat):
            def __init__(self, error):
                super().__init__("x")
                self._error = error

            async def chat(self, messages, **kwargs):
                raise self._error

        client = _indexed_client(tmp_path, Boom(exc))
        resp = client.post(
            "/api/qa/ask", json={"question": "问题?", "paper_ids": ["attn-1706"], "top_k": 3}
        )
        assert resp.status_code == 502
        assert "生成服务不可用" in resp.json()["detail"]
