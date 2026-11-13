"""图表 API 测试：四类 option schema、CSV 输入与 caption 降级（零网络）。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import writing
from app.services.chat_service import ChatAPIError
from app.services.writing_chart import ChartEngine, check_option_schema

ROWS = [
    {"month": "Jan", "sales": 10, "profit": 3, "growth": 0.5},
    {"month": "Feb", "sales": 20, "profit": 5, "growth": 0.8},
    {"month": "Mar", "sales": 15, "profit": 4, "growth": 0.6},
]


class StubChat:
    """固定回复或固定抛错的 ChatService 桩。"""

    def __init__(self, reply=None, error=None) -> None:
        self.reply = reply
        self.error = error

    async def chat(self, messages, **kwargs):
        if self.error is not None:
            raise self.error
        return self.reply


def make_client(chat) -> TestClient:
    writing.set_chart_engine(ChartEngine(chat=chat))
    app = FastAPI()
    app.include_router(writing.router)
    return TestClient(app)


@pytest.mark.parametrize("chart_type", ["line", "bar", "radar", "scatter"])
def test_chart_types_schema_and_caption(chart_type):
    """四类图 option schema 零错误，caption 非空。"""
    chat = StubChat(reply="Figure 1: Monthly performance across three metrics.")
    client = make_client(chat)
    resp = client.post(
        "/api/writing/chart",
        json={"rows": ROWS, "chart_type": chart_type, "title": "Monthly performance"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert check_option_schema(data["option"], chart_type) == []
    assert data["caption"].strip()


def test_chart_csv_text():
    """csv_text 解析为类别轴与数值序列。"""
    chat = StubChat(reply="Figure 1: Monthly sales.")
    client = make_client(chat)
    csv_text = "month,sales\nJan,10\nFeb,20\nMar,15\n"
    resp = client.post("/api/writing/chart", json={"csv_text": csv_text, "chart_type": "line"})
    assert resp.status_code == 200
    option = resp.json()["option"]
    assert option["xAxis"]["data"] == ["Jan", "Feb", "Mar"]
    assert option["series"][0]["data"] == [10, 20, 15]


def test_chart_caption_fallback_when_llm_fails():
    """LLM 失败 → 确定性 Figure 1 降级图注。"""
    chat = StubChat(error=ChatAPIError("bad key"))
    client = make_client(chat)
    resp = client.post(
        "/api/writing/chart",
        json={"rows": ROWS, "chart_type": "bar", "title": "Monthly performance"},
    )
    assert resp.status_code == 200
    assert resp.json()["caption"] == "Figure 1: Monthly performance"
