"""模块一测试：pdf_parse.py —— ParsedPaper 契约与解析启发式。"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.paper_models import validate_contract
from app.services.pdf_parse import body_text_of, load_parsed, parse_pdf, save_parsed

CORPUS = Path("D:/deep/czb_assets/testdata")
V1 = CORPUS / "1706.03762v1.pdf"
BLOCK_TYPES = {"text", "heading", "table", "formula", "figure"}


def test_contract_schema_v1():
    contract = parse_pdf(V1)
    assert validate_contract(contract) == []
    assert "Attention Is All You Need" in contract["title"]
    assert contract["num_pages"] == 15
    assert len(contract["pages"]) == 15
    for i, page in enumerate(contract["pages"]):
        assert page["page_no"] == i + 1
        for block in page["blocks"]:
            assert set(block.keys()) <= {"type", "text", "level"}
            assert block["type"] in BLOCK_TYPES
            if block["type"] == "heading":
                assert isinstance(block["level"], int) and 1 <= block["level"] <= 3
            else:
                assert "level" not in block
    assert len(contract["references"]) >= 30
    for ref in contract["references"]:
        assert set(ref.keys()) == {"index", "raw", "title", "arxiv_id"}
        assert isinstance(ref["index"], int)
        assert ref["raw"].strip()
    assert contract["meta"]["source_filename"] == "1706.03762v1.pdf"
    assert re.fullmatch(r"[0-9a-f]{64}", contract["meta"]["sha256"])
    assert contract["paper_id"] == contract["meta"]["sha256"][:12]
    arxiv_ids = [r["arxiv_id"] for r in contract["references"] if r["arxiv_id"]]
    assert arxiv_ids
    assert all(re.fullmatch(r"\d{4}\.\d{4,5}", a) for a in arxiv_ids)


def test_heading_tree_levels():
    contract = parse_pdf(V1)
    headings = {
        b["text"]: b["level"]
        for p in contract["pages"]
        for b in p["blocks"]
        if b["type"] == "heading"
    }
    assert headings.get("Abstract") == 1
    assert headings.get("References") == 1
    assert headings.get("1 Introduction") == 1
    assert headings.get("3.1 Encoder and Decoder Stacks") == 2
    assert headings.get("3.2.1 Scaled Dot-Product Attention") == 3
    assert all(1 <= lvl <= 3 for lvl in headings.values())
    assert not any(t.lower().startswith("arxiv:") for t in headings)


def test_reference_entry_parsing():
    contract = parse_pdf(V1)
    refs = contract["references"]
    assert [r["index"] for r in refs] == list(range(1, len(refs) + 1))
    titled = [r["title"] for r in refs if r["title"]]
    assert len(titled) >= int(0.3 * len(refs))


def test_save_load_roundtrip(tmp_path):
    contract = parse_pdf(V1)
    path = save_parsed(contract, tmp_path)
    assert path == tmp_path / f"{contract['paper_id']}.json"
    loaded = load_parsed(tmp_path, contract["paper_id"])
    assert loaded == contract
    assert body_text_of(contract).strip()
