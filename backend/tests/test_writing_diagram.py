"""示意图 API 测试：三 kind 合法代码与非法输出（零网络）。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import writing
from app.services.writing_diagram import DiagramEngine

FENCE = chr(96) * 3


class StubChat:
    """固定回复的 ChatService 桩。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def chat(self, messages, **kwargs):
        return self.reply


def make_client(chat) -> TestClient:
    writing.set_diagram_engine(DiagramEngine(chat=chat))
    app = FastAPI()
    app.include_router(writing.router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("tikz", "\\begin{tikzpicture}\n\\node (a) {Encoder};\n\\end{tikzpicture}"),
        ("graphviz", "digraph G {\n  encoder -> decoder;\n}"),
        ("mermaid", "graph TD;\n  A-->B;"),
    ],
)
def test_diagram_kinds_return_valid_code(kind, code):
    """LLM 围栏输出被剥离，返回裸代码 + 非空 usage_hint。"""
    chat = StubChat(reply=FENCE + kind + "\n" + code + "\n" + FENCE)
    client = make_client(chat)
    resp = client.post(
        "/api/writing/diagram", json={"kind": kind, "description": "编码器-解码器架构"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == kind
    assert data["code"] == code
    assert data["usage_hint"]


def test_diagram_invalid_code_502():
    """LLM 输出非法骨架 → 502。"""
    chat = StubChat(reply="好的，我来解释一下这个图：先画两个节点……")
    client = make_client(chat)
    resp = client.post("/api/writing/diagram", json={"kind": "mermaid", "description": "x"})
    assert resp.status_code == 502
