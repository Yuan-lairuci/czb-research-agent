"""模块一测试：ocr.py —— 无文本层检测与 OCR 管线。"""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf

from app.services.ocr import first_page_text, ocr_pdf_page, page_has_text

CORPUS = Path("D:/deep/czb_assets/testdata")
IMG = CORPUS / "1706.03762v1_img.pdf"
V1 = CORPUS / "1706.03762v1.pdf"


def test_no_text_layer_detection():
    with pymupdf.open(IMG) as doc:
        assert not page_has_text(doc[0])
    with pymupdf.open(V1) as doc:
        assert page_has_text(doc[0])


def test_text_layer_result_shape():
    r = ocr_pdf_page(V1, page_no=0)
    assert r["needs_ocr"] is False
    assert r["engine"] == "text-layer"
    assert "Attention Is All You Need" in r["text"]


def test_ocr_first_page_readable():
    text = first_page_text(IMG)
    assert len(text) >= 100
    assert "attention" in text.lower()


def test_engine_none_placeholder():
    with mock.patch("app.services.ocr.get_engine", return_value=None):
        r = ocr_pdf_page(IMG, page_no=0)
    assert r["needs_ocr"] is True
    assert r["engine"] == "none"
    assert r["text"] == ""
