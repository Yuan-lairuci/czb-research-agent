"""大纲生成 API 测试：结构完整性与 LLM 畸形输出（零网络）。"""
from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import writing
from app.services.writing_outline import DEFAULT_SECTIONS, OutlineEngine


class StubChat:
    """固定回复的 ChatService 桩。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply


def make_client(reply: str) -> TestClient:
    writing.set_outline_engine(OutlineEngine(chat=StubChat(reply)))
    app = FastAPI()
    app.include_router(writing.router)
    return TestClient(app)


def test_outline_default_six_sections():
    """默认六节齐全，每节含要点。"""
    sections = [{"title": t, "points": [f"{t} 要点一", f"{t} 要点二"]} for t in DEFAULT_SECTIONS]
    client = make_client(json.dumps({"sections": sections}, ensure_ascii=False))
    resp = client.post("/api/writing/outline", json={"idea": "用注意力机制改进时序预测"})
    assert resp.status_code == 200
    data = resp.json()
    assert [s["title"] for s in data["sections"]] == DEFAULT_SECTIONS
    assert all(len(s["points"]) == 2 for s in data["sections"])


def test_outline_custom_sections_follow_request_order():
    """自定义小节大小写不敏感匹配，且按请求顺序返回。"""
    reply = json.dumps(
        {
            "sections": [
                {"title": "method", "points": ["方法要点"]},
                {"title": "Abstract", "points": ["摘要要点一", "摘要要点二"]},
            ]
        },
        ensure_ascii=False,
    )
    client = make_client(reply)
    resp = client.post(
        "/api/writing/outline",
        json={"idea": "x", "sections": ["Abstract", "Method"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [s["title"] for s in body["sections"]] == ["Abstract", "Method"]
    assert body["sections"][0]["points"] == ["摘要要点一", "摘要要点二"]


def test_outline_missing_section_502():
    """LLM 输出缺节 → 502。"""
    reply = json.dumps({"sections": [{"title": "Abstract", "points": ["a"]}]})
    client = make_client(reply)
    resp = client.post("/api/writing/outline", json={"idea": "x"})
    assert resp.status_code == 502


def test_outline_bad_json_502():
    """LLM 输出非 JSON → 502。"""
    client = make_client("这不是 JSON")
    resp = client.post("/api/writing/outline", json={"idea": "x"})
    assert resp.status_code == 502
