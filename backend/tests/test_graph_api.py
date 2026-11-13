"""模块五测试：api/graph.py —— 五端点装配、404、LLM 注入。"""

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.graph import create_graph_router, set_llm


def make_contract(paper_id: str, title: str, refs: list[dict[str, Any]] | None = None,
                  blocks: list[dict[str, Any]] | None = None,
                  source_filename: str = "") -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "title": title,
        "num_pages": 1,
        "pages": [{"page_no": 1, "blocks": blocks or []}],
        "references": refs or [],
        "meta": {"sha256": "0" * 64, "source_filename": source_filename},
    }


def write_parsed(parsed_dir: Path, contract: dict[str, Any]) -> None:
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / f"{contract['paper_id']}.json").write_text(
        json.dumps(contract, ensure_ascii=False), encoding="utf-8"
    )


class ScriptedChat:
    """按脚本返回回答的 chat stub。"""

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return self.answer


def build_client(tmp_path: Path) -> TestClient:
    parsed_dir = tmp_path / "parsed"
    write_parsed(
        parsed_dir,
        make_contract(
            "a1", "Paper A",
            refs=[{"index": 1, "raw": "B", "title": "Paper B", "arxiv_id": None}],
            blocks=[{"type": "text", "text": "We build on Paper B [1]."}],
        ),
    )
    write_parsed(
        parsed_dir,
        make_contract(
            "b1", "Paper B",
            blocks=[{"type": "heading", "text": "6 Future Work", "level": 1},
                    {"type": "text", "text": "We leave crosslingual evaluation for future study."}],
        ),
    )
    app = FastAPI()
    app.include_router(create_graph_router(data_dir=tmp_path))
    return TestClient(app)


def test_api_build_analyze_get(tmp_path):
    """build → analyze → get 全链路（LLM=None 模板解读）。"""
    set_llm(None)
    client = build_client(tmp_path)
    resp = client.post("/api/graph/build", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["num_nodes"] == 2 and body["num_edges"] == 1

    resp2 = client.post("/api/graph/analyze", json={"graph_id": body["graph_id"]})
    assert resp2.status_code == 200
    metrics = resp2.json()["metrics"]
    assert {m["paper_id"] for m in metrics} == {"a1", "b1"}
    assert all(m["interpretation"] for m in metrics)
    categories = {m["paper_id"]: m["category"] for m in metrics}
    assert categories["b1"] == "基石论文"

    resp3 = client.get(f"/api/graph/{body['graph_id']}")
    assert resp3.status_code == 200
    data = resp3.json()
    assert len(data["nodes"]) == 2 and len(data["edges"]) == 1
    assert data["metrics"], "analyze 后 get 应带指标"


def test_api_review_and_future_work(tmp_path):
    """review（stub LLM）与 future-work 端点可用。"""
    answer = (
        "## 背景\n早期方法 [1]。\n## 方法演进\n演进 [2]。\n"
        "## 代表工作\n代表 [1]。\n## 现状与挑战\n挑战 [99]。"
    )
    set_llm(ScriptedChat(answer))
    client = build_client(tmp_path)
    gid = client.post("/api/graph/build", json={}).json()["graph_id"]
    resp = client.post("/api/graph/review", json={"graph_id": gid, "topic": "主题"})
    assert resp.status_code == 200
    review = resp.json()
    assert review["generated_by"] == "llm"
    assert len(review["sections"]) == 4
    assert review["dropped_citations"]
    assert "[99]" not in json.dumps(review["sections"], ensure_ascii=False)

    resp2 = client.post("/api/graph/future-work", json={"graph_id": gid})
    assert resp2.status_code == 200
    timeline = resp2.json()["timeline"]
    assert timeline and timeline[0]["paper_id"] in ("a1", "b1")
    assert set(timeline[0].keys()) >= {"item", "proposed_by", "year", "status", "evidence"}


def test_api_404_and_subset(tmp_path):
    """未知 graph 404；paper_ids 子集建图。"""
    set_llm(None)
    client = build_client(tmp_path)
    assert client.get("/api/graph/nope").status_code == 404
    assert client.post("/api/graph/analyze", json={"graph_id": "nope"}).status_code == 404
    assert client.post("/api/graph/future-work", json={"graph_id": "nope"}).status_code == 404
    assert client.post("/api/graph/build", json={"paper_ids": ["nope"]}).status_code == 404
    resp = client.post("/api/graph/build", json={"paper_ids": ["b1"]})
    assert resp.status_code == 200
    assert resp.json()["num_nodes"] == 1 and resp.json()["num_edges"] == 0
