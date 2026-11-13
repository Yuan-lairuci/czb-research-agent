"""zh2en 翻译管线测试（零网络，复用 translate_llm）。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import writing
from app.services.translate_llm import TranslateEngine
from app.services.translate_store import TranslateStore
from tests.test_translate_llm import FakeChat


def test_zh2en_pipeline(tmp_path):
    """POST /api/writing/zh2en 走 TranslateEngine 管线返回译文与引擎标签。"""
    chat = FakeChat()
    writing.set_translator(
        TranslateEngine(
            chat=chat,
            store=TranslateStore(db_path=tmp_path / "cache.db"),
            parsed_dir=tmp_path,
        )
    )
    app = FastAPI()
    app.include_router(writing.router)
    client = TestClient(app)
    resp = client.post("/api/writing/zh2en", json={"text": "深度学习"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["translated"].startswith("译文:")
    assert "深度学习" in data["translated"]
    assert data["engine"] == "fake-model"
