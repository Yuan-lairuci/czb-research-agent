"""扫描件 OCR 管线（模块一）。

检测逻辑：逐页 get_text().strip() 长度为 0 判无文本层。
引擎优先级：rapidocr-onnxruntime -> PaddleOCR -> OpenAI 兼容 VLM。
全部不可用时返回 {"needs_ocr": true, "engine": "none"} 结构化占位。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import pymupdf

from app.services.pdf_parse import paper_id_from_sha, sha256_of_file


class OCREngine(ABC):
    """OCR 引擎抽象。"""

    name: str = "base"

    @abstractmethod
    def recognize(self, page_image: bytes) -> str:
        """识别整页图像（PNG bytes），返回纯文本。"""


class RapidOCREngine(OCREngine):
    """rapidocr-onnxruntime：轻量 ONNX 引擎（默认首选）。"""

    name = "rapidocr-onnxruntime"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # 惰性导入，未安装不阻塞其它引擎

        self._ocr = RapidOCR()

    def recognize(self, page_image: bytes) -> str:
        result, _ = self._ocr(page_image)
        if not result:
            return ""
        return "\n".join(str(line[1]) for line in result)


class PaddleOCREngine(OCREngine):
    """PaddleOCR 备选引擎。"""

    name = "paddleocr"

    def __init__(self) -> None:
        from paddleocr import PaddleOCR  # 惰性导入

        self._ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)

    def recognize(self, page_image: bytes) -> str:
        import numpy as np  # 惰性导入

        arr = np.frombuffer(page_image, dtype=np.uint8)
        result = self._ocr.ocr(arr, cls=True)
        lines: list[str] = []
        for page in result or []:
            for entry in page or []:
                if entry:
                    lines.append(str(entry[1][0]))
        return "\n".join(lines)


class VLMOCREngine(OCREngine):
    """OpenAI 兼容 VLM：env VLM_BASE_URL / VLM_MODEL / VLM_API_KEY（缺省回退 LLM_API_KEY）。"""

    name = "vlm"

    def __init__(self) -> None:
        self._base = (os.environ.get("VLM_BASE_URL") or "").rstrip("/")
        self._model = os.environ.get("VLM_MODEL") or ""
        self._key = os.environ.get("VLM_API_KEY") or os.environ.get("LLM_API_KEY") or ""

    @property
    def available(self) -> bool:
        return bool(self._base and self._model and self._key)

    def recognize(self, page_image: bytes) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请逐字转录本页全部文字，只输出转录文本。"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(page_image).decode("ascii")
                            },
                        },
                    ],
                }
            ],
        }
        req = urllib.request.Request(
            self._base + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - 用户自配端点
            body = json.loads(resp.read().decode("utf-8"))
        return str(body["choices"][0]["message"]["content"])


_ENGINE_CACHE: dict[str, Any] = {}


def get_engine() -> OCREngine | None:
    """按优先级返回可用引擎；全部不可用返回 None。"""
    if "resolved" in _ENGINE_CACHE:
        return _ENGINE_CACHE["resolved"]

    for factory in (RapidOCREngine, PaddleOCREngine):
        try:
            engine = factory()
            _ENGINE_CACHE["resolved"] = engine
            return engine
        except Exception:  # noqa: BLE001 - 引擎缺失/初始化失败逐级降级
            continue

    try:
        vlm = VLMOCREngine()
        if vlm.available:
            _ENGINE_CACHE["resolved"] = vlm
            return vlm
    except Exception:  # noqa: BLE001
        pass

    _ENGINE_CACHE["resolved"] = None
    return None


def page_has_text(page: pymupdf.Page) -> bool:
    """逐页检测文本层：get_text().strip() 长度为 0 判无文本层。"""
    return bool(page.get_text().strip())


def render_page_png(page: pymupdf.Page, dpi: int = 150) -> bytes:
    """整页渲染为 PNG bytes 供 OCR 引擎识别。"""
    return page.get_pixmap(dpi=dpi).tobytes("png")


def ocr_pdf_page(
    pdf_path: Path | str,
    page_no: int = 0,
    engine: OCREngine | None = None,
) -> dict[str, Any]:
    """单页 OCR 结果（OcrResult 形状 dict）：有文本层直接取文本，否则走引擎。"""
    pdf_path = Path(pdf_path)
    paper_id = paper_id_from_sha(sha256_of_file(pdf_path))
    doc = pymupdf.open(pdf_path)
    try:
        if page_no < 0 or page_no >= doc.page_count:
            raise ValueError(f"page_no {page_no} out of range (total {doc.page_count})")
        page = doc[page_no]
        if page_has_text(page):
            return {
                "paper_id": paper_id,
                "page_no": page_no,
                "needs_ocr": False,
                "engine": "text-layer",
                "text": page.get_text(),
            }
        eff_engine = engine if engine is not None else get_engine()
        if eff_engine is None:
            return {
                "paper_id": paper_id,
                "page_no": page_no,
                "needs_ocr": True,
                "engine": "none",
                "text": "",
            }
        text = eff_engine.recognize(render_page_png(page))
        return {
            "paper_id": paper_id,
            "page_no": page_no,
            "needs_ocr": True,
            "engine": eff_engine.name,
            "text": text,
        }
    finally:
        doc.close()


def first_page_text(pdf_path: Path | str) -> str:
    """首页文本（文本层或 OCR），便捷入口。"""
    return str(ocr_pdf_page(pdf_path, page_no=0).get("text", ""))
