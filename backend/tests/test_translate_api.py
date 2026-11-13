"""翻译 API 契约测试：独立 FastAPI app 挂载 router，FakeChat 零网络。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.translate import router, set_engine
from app.services.translate_llm import TranslateEngine
from app.services.translate_store import TranslateStore
from tests.test_translate_llm import FakeChat, write_paper


def make_client(tmp_path: Path, chunk_size: int = 3) -> tuple[TestClient, FakeChat]:
    """独立 FastAPI app 挂载本 router + 注入 FakeChat 引擎（不依赖 main.py）。"""
    chat = FakeChat(chunk_size=chunk_size)
    engine = TranslateEngine(
        chat=chat, store=TranslateStore(db_path=tmp_path / "cache.db"), parsed_dir=tmp_path
    )
    set_engine(engine)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), chat


def parse_sse(body: str) -> tuple[list[dict], dict | None, bool]:
    """解析 SSE 文本：返回 (delta 帧列表, done 帧, 是否以 [DONE] 终止)。"""
    deltas: list[dict] = []
    done_frame: dict | None = None
    saw_done = False
    for raw in body.split("\n\n"):
        line = raw.strip()
        if not line.startswith("data: "):
            continue
        payload = line[len("data: ") :]
        if payload == "[DONE]":
            saw_done = True
            continue
        frame = json.loads(payload)
        if frame.get("done"):
            done_frame = frame
        else:
            deltas.append(frame)
    return deltas, done_frame, saw_done


def test_text_json_roundtrip(tmp_path: Path) -> None:
    """非流式 JSON：{translated, engine}，缓存命中第二次 engine=cache 且不加调用。"""
    client, chat = make_client(tmp_path)
    r1 = client.post(
        "/api/translate/text", json={"text": "你好", "direction": "zh2en"}
    )
    assert r1.status_code == 200
    assert r1.json() == {"translated": "译文:你好", "engine": "fake-model"}

    r2 = client.post(
        "/api/translate/text", json={"text": "你好", "direction": "zh2en"}
    )
    assert r2.json() == {"translated": "译文:你好", "engine": "cache"}
    assert len(chat.calls) == 1


def test_text_sse_stream(tmp_path: Path) -> None:
    """SSE 流式：增量 delta 帧 + done 全量帧 + [DONE] 终止，占位符跨块无损还原。"""
    text = "公式 $E=mc^2$ 与 3.5km。"
    client, _ = make_client(tmp_path, chunk_size=3)
    r = client.post(
        "/api/translate/text",
        json={"text": text, "direction": "zh2en", "stream": True},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]

    deltas, done_frame, saw_done = parse_sse(r.text)
    assert deltas and all(f["done"] is False for f in deltas)
    joined = "".join(f["delta"] for f in deltas)
    assert joined == "译文:" + text
    assert done_frame is not None
    assert done_frame["done"] is True
    assert done_frame["translated"] == joined
    assert done_frame["engine"] == "fake-model"
    assert saw_done


def test_page_block_alignment(tmp_path: Path) -> None:
    """POST /page 契约：逐块对齐 [{block_index, src_text, dst_text}]。"""
    write_paper(
        tmp_path,
        "pa",
        [
            {
                "page_no": 1,
                "blocks": [
                    {"type": "text", "text": "块一"},
                    {"type": "text", "text": "块二"},
                ],
            }
        ],
    )
    client, _ = make_client(tmp_path)
    r = client.post(
        "/api/translate/page",
        json={"paper_id": "pa", "page_no": 1, "direction": "zh2en"},
    )
    assert r.status_code == 200
    blocks = r.json()
    assert [b["block_index"] for b in blocks] == [0, 1]
    assert blocks[0] == {"block_index": 0, "src_text": "块一", "dst_text": "译文:块一"}
    assert blocks[1]["dst_text"] == "译文:块二"


def test_paper_incremental_progress(tmp_path: Path) -> None:
    """GET /paper 整篇进度：fill=false 不触达 LLM；fill=true 补译且已译页不重译。"""
    write_paper(
        tmp_path,
        "pb",
        [
            {"page_no": 1, "blocks": [{"type": "text", "text": "页一"}]},
            {"page_no": 2, "blocks": [{"type": "text", "text": "页二"}]},
        ],
    )
    client, chat = make_client(tmp_path)

    r0 = client.get("/api/translate/paper/pb?direction=zh2en&fill=false")
    assert r0.status_code == 200
    assert r0.json()["translated_pages"] == 0
    assert len(chat.calls) == 0

    r1 = client.get("/api/translate/paper/pb?direction=zh2en&fill=true")
    assert r1.json()["translated_pages"] == 2
    assert r1.json()["done"] is True
    assert len(chat.calls) == 2

    r2 = client.get("/api/translate/paper/pb?direction=zh2en&fill=true")
    assert r2.json()["translated_pages"] == 2
    assert len(chat.calls) == 2  # 已译页不重复调 LLM


def test_bad_inputs_4xx(tmp_path: Path) -> None:
    """坏输入 4xx：方向非法/空文本/页码越界 → 422；未知论文 → 404。"""
    client, _ = make_client(tmp_path)
    assert (
        client.post(
            "/api/translate/text", json={"text": "hi", "direction": "jp2en"}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/translate/text", json={"text": "", "direction": "zh2en"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/translate/page",
            json={"paper_id": "pa", "page_no": 0, "direction": "zh2en"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/translate/page",
            json={"paper_id": "missing", "page_no": 1, "direction": "zh2en"},
        ).status_code
        == 404
    )


def test_paper_path_traversal_rejected(tmp_path: Path) -> None:
    """GET /paper/{paper_id} 路径穿越（..）被 pattern 校验拒绝 422。"""
    client, _ = make_client(tmp_path)
    r = client.get("/api/translate/paper/%2E%2E?direction=zh2en")
    assert r.status_code == 422


def test_paper_page_out_of_range_404(tmp_path: Path) -> None:
    """论文存在但页码越界 → 404。"""
    write_paper(
        tmp_path, "pc", [{"page_no": 1, "blocks": [{"type": "text", "text": "hi"}]}]
    )
    client, _ = make_client(tmp_path)
    r = client.post(
        "/api/translate/page",
        json={"paper_id": "pc", "page_no": 9, "direction": "zh2en"},
    )
    assert r.status_code == 404
