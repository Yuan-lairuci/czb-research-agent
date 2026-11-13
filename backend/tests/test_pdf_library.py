"""模块一测试：pdf_library.py —— CRUD 生命周期、上传限制、去重与 OCR 端点。"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.pdf_library import create_library_router
from app.models.paper_models import validate_contract

CORPUS = Path("D:/deep/czb_assets/testdata")
V1 = CORPUS / "1706.03762v1.pdf"
V7 = CORPUS / "1706.03762v7.pdf"


def make_client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_library_router(data_dir=tmp_path / "data"))
    return TestClient(app)


def test_upload_list_get_delete_lifecycle(tmp_path):
    client = make_client(tmp_path)
    src = tmp_path / "upload.pdf"
    shutil.copyfile(V1, src)
    resp = client.post(
        "/api/library/papers",
        files={"file": ("1706.03762v1.pdf", src.read_bytes(), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    info = resp.json()
    assert len(info["paper_id"]) == 12
    assert info["parse_status"] == "parsed"
    assert "Attention" in info["title"]
    assert info["num_pages"] == 15

    lst = client.get("/api/library/papers").json()["papers"]
    assert [p["paper_id"] for p in lst] == [info["paper_id"]]

    contract = client.get(f"/api/library/papers/{info['paper_id']}/parsed").json()
    assert validate_contract(contract) == []

    assert client.delete(f"/api/library/papers/{info['paper_id']}").status_code == 200
    assert client.get("/api/library/papers").json()["papers"] == []
    assert client.get(f"/api/library/papers/{info['paper_id']}/parsed").status_code == 404


def test_download_file_endpoint(tmp_path):
    client = make_client(tmp_path)
    src = tmp_path / "dl.pdf"
    shutil.copyfile(V1, src)
    resp = client.post(
        "/api/library/papers",
        files={"file": ("1706.03762v1.pdf", src.read_bytes(), "application/pdf")},
    )
    pid = resp.json()["paper_id"]
    r = client.get(f"/api/library/papers/{pid}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "1706.03762v1.pdf" in r.headers["content-disposition"]
    assert r.content[:5] == b"%PDF-"


def test_download_file_404(tmp_path):
    client = make_client(tmp_path)
    missing = client.get("/api/library/papers/deadbeef0000/file")
    assert missing.status_code == 404
    assert "not found" in missing.json()["detail"]

    src = tmp_path / "dl2.pdf"
    shutil.copyfile(V1, src)
    resp = client.post(
        "/api/library/papers",
        files={"file": ("1706.03762v1.pdf", src.read_bytes(), "application/pdf")},
    )
    pid = resp.json()["paper_id"]
    (tmp_path / "data" / "library" / f"{pid}.pdf").unlink()
    gone = client.get(f"/api/library/papers/{pid}/file")
    assert gone.status_code == 404
    assert "not found" in gone.json()["detail"]


def test_upload_rejects_non_pdf(tmp_path):
    client = make_client(tmp_path)
    resp = client.post(
        "/api/library/papers", files={"file": ("evil.txt", b"not a pdf", "text/plain")}
    )
    assert resp.status_code == 415


def test_upload_size_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBRARY_MAX_UPLOAD_MB", "0")
    client = make_client(tmp_path)
    resp = client.post(
        "/api/library/papers", files={"file": ("big.pdf", V1.read_bytes(), "application/pdf")}
    )
    assert resp.status_code == 413


def test_upload_empty_file_rejected(tmp_path):
    client = make_client(tmp_path)
    resp = client.post("/api/library/papers", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert resp.status_code == 400


def test_duplicate_upload_idempotent(tmp_path):
    client = make_client(tmp_path)
    payload = V1.read_bytes()
    r1 = client.post("/api/library/papers", files={"file": ("a.pdf", payload, "application/pdf")})
    r2 = client.post(
        "/api/library/papers", files={"file": ("a_again.pdf", payload, "application/pdf")}
    )
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["paper_id"] == r2.json()["paper_id"]
    assert len(client.get("/api/library/papers").json()["papers"]) == 1


def test_dedup_scan_endpoint_target_case(tmp_path):
    client = make_client(tmp_path)
    ids = {}
    for name, src in (("1706.03762v1.pdf", V1), ("1706.03762v7.pdf", V7)):
        resp = client.post(
            "/api/library/papers", files={"file": (name, src.read_bytes(), "application/pdf")}
        )
        assert resp.status_code == 201, resp.text
        ids[name] = resp.json()["paper_id"]

    scan = client.post("/api/library/dedup/scan")
    assert scan.status_code == 200
    body = scan.json()
    assert len(body["groups"]) == 1
    group = body["groups"][0]
    assert sorted(group["paper_ids"]) == sorted([ids["1706.03762v1.pdf"], ids["1706.03762v7.pdf"]])
    assert group["same_work"] is True
    assert group["suggested_keep"] == ids["1706.03762v7.pdf"]
    assert "v7" in group["reason"]

    lst = {p["paper_id"]: p for p in client.get("/api/library/papers").json()["papers"]}
    assert lst[ids["1706.03762v1.pdf"]]["dedup_group"] == "g1"
    assert lst[ids["1706.03762v7.pdf"]]["dedup_group"] == "g1"


def test_ocr_endpoint_text_layer(tmp_path):
    client = make_client(tmp_path)
    resp = client.post(
        "/api/library/papers",
        files={"file": ("1706.03762v1.pdf", V1.read_bytes(), "application/pdf")},
    )
    pid = resp.json()["paper_id"]
    r = client.get(f"/api/library/papers/{pid}/ocr", params={"page_no": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["needs_ocr"] is False
    assert "Attention Is All You Need" in body["text"]
