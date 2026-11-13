"""模块一 API：/api/library —— 文献库 CRUD、去重扫描、单页 OCR。

独立可测：create_library_router(data_dir=...) 可注入数据目录；
默认 router 使用 backend/data/（library/ 存 PDF + SQLite，parsed/ 存契约 JSON）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.models.paper_models import (
    DedupScanResponse,
    OcrResult,
    PaperDB,
    PaperInfo,
    PaperListResponse,
)
from app.services import ocr as ocr_service
from app.services import pdf_dedup, pdf_parse

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_UPLOAD_CHUNK = 1024 * 1024
_DEFAULT_MAX_UPLOAD_MB = 200


def create_library_router(data_dir: Path | str | None = None) -> APIRouter:
    """构建文献库路由；data_dir 缺省为 backend/data/。"""
    data_root = Path(data_dir) if data_dir is not None else _DEFAULT_DATA_DIR
    library_dir = data_root / "library"
    parsed_dir = data_root / "parsed"
    library_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    db = PaperDB(library_dir / "library.db")

    router = APIRouter(prefix="/api/library")

    def _info_from_row(row: dict[str, Any]) -> PaperInfo:
        return PaperInfo(
            paper_id=row["paper_id"],
            title=row["title"],
            num_pages=row["num_pages"],
            parse_status=row["parse_status"],
            dedup_group=row["dedup_group"],
            source_filename=row["source_filename"],
            sha256=row["sha256"],
            uploaded_at=row["uploaded_at"],
        )

    def _require_row(paper_id: str) -> dict[str, Any]:
        row = db.get(paper_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"paper {paper_id} not found")
        return row

    @router.post("/papers", response_model=PaperInfo, status_code=201)
    async def upload_paper(file: Annotated[UploadFile, File()]) -> PaperInfo:
        filename = file.filename or ""
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=415, detail="only .pdf uploads are supported")
        max_mb = int(os.environ.get("LIBRARY_MAX_UPLOAD_MB", str(_DEFAULT_MAX_UPLOAD_MB)))
        limit = max_mb * _UPLOAD_CHUNK
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await file.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if limit >= 0 and total > limit:
                raise HTTPException(status_code=413, detail=f"upload exceeds {max_mb} MB limit")
            chunks.append(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="empty file")

        import hashlib

        payload = b"".join(chunks)
        sha256 = hashlib.sha256(payload).hexdigest()
        paper_id = sha256[:12]

        existing = db.get(paper_id)
        if existing is not None:
            return _info_from_row(existing)

        saved = library_dir / f"{paper_id}.pdf"
        saved.write_bytes(payload)

        parse_status = "parsed"
        try:
            contract = pdf_parse.parse_pdf(saved, source_filename=filename)
            pdf_parse.save_parsed(contract, parsed_dir)
            title = contract["title"]
            num_pages = contract["num_pages"]
        except Exception:  # noqa: BLE001 - 解析失败保留文件与记录
            parse_status = "failed"
            title = filename
            num_pages = 0

        record = {
            "paper_id": paper_id,
            "title": title,
            "num_pages": num_pages,
            "parse_status": parse_status,
            "dedup_group": None,
            "source_filename": filename,
            "sha256": sha256,
            "uploaded_at": PaperDB.now_iso(),
        }
        db.insert(record)
        return _info_from_row(record)

    @router.get("/papers", response_model=PaperListResponse)
    async def list_papers() -> PaperListResponse:
        return PaperListResponse(papers=[_info_from_row(row) for row in db.list_all()])

    @router.get("/papers/{paper_id}", response_model=PaperInfo)
    async def get_paper(paper_id: str) -> PaperInfo:
        return _info_from_row(_require_row(paper_id))

    @router.get("/papers/{paper_id}/parsed")
    async def get_parsed(paper_id: str) -> dict[str, Any]:
        _require_row(paper_id)
        contract = pdf_parse.load_parsed(parsed_dir, paper_id)
        if contract is None:
            raise HTTPException(status_code=404, detail=f"parsed contract for {paper_id} not found")
        return contract

    @router.delete("/papers/{paper_id}")
    async def delete_paper(paper_id: str) -> dict[str, str]:
        _require_row(paper_id)
        db.delete(paper_id)
        for path in (library_dir / f"{paper_id}.pdf", parsed_dir / f"{paper_id}.json"):
            if path.exists():
                path.unlink()
        return {"deleted": paper_id}

    @router.post("/dedup/scan", response_model=DedupScanResponse)
    async def dedup_scan() -> DedupScanResponse:
        contracts: list[dict[str, Any]] = []
        for row in db.list_all():
            contract = pdf_parse.load_parsed(parsed_dir, str(row["paper_id"]))
            if contract is not None:
                contracts.append(contract)
        groups = pdf_dedup.scan_duplicates(contracts)
        db.clear_dedup_groups()
        for idx, group in enumerate(groups, start=1):
            gid = f"g{idx}"
            for pid in group["paper_ids"]:
                db.set_dedup_group(pid, gid)
        return DedupScanResponse(groups=groups)

    @router.get("/papers/{paper_id}/ocr", response_model=OcrResult)
    async def ocr_paper_page(
        paper_id: str,
        page_no: int = Query(0, ge=0),
    ) -> OcrResult:
        row = _require_row(paper_id)
        saved = library_dir / f"{paper_id}.pdf"
        if not saved.exists():
            raise HTTPException(status_code=404, detail=f"file for {paper_id} not found")
        result = ocr_service.ocr_pdf_page(saved, page_no=page_no)
        result["paper_id"] = row["paper_id"]
        return OcrResult(**result)

    return router


router = create_library_router()
