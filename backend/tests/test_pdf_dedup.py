"""模块一测试：pdf_dedup.py —— 语义去重（版本识别）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.pdf_dedup import (
    compare_contracts,
    jaccard,
    normalize_title,
    scan_duplicates,
    shingle_set,
    suggest_keep,
)
from app.services.pdf_parse import parse_pdf

CORPUS = Path("D:/deep/czb_assets/testdata")
V1 = CORPUS / "1706.03762v1.pdf"
V7 = CORPUS / "1706.03762v7.pdf"
BERT = CORPUS / "papers" / "1810.04805.pdf"
ROBERTA = CORPUS / "papers" / "1907.11692.pdf"


def test_dedup_target_case_v1_v7():
    c1 = parse_pdf(V1)
    c7 = parse_pdf(V7)
    assert c1["paper_id"] != c7["paper_id"]
    groups = scan_duplicates([c1, c7])
    assert len(groups) == 1
    group = groups[0]
    assert sorted(group["paper_ids"]) == sorted([c1["paper_id"], c7["paper_id"]])
    assert group["same_work"] is True
    assert group["suggested_keep"] == c7["paper_id"], "必须建议保留 v7"
    assert "v7" in group["reason"]


def test_dedup_different_papers_not_grouped():
    cb = parse_pdf(BERT)
    cr = parse_pdf(ROBERTA)
    assert scan_duplicates([cb, cr]) == []


def test_normalize_title():
    assert normalize_title("Attention Is All You Need v7!") == "attention is all you need"
    assert (
        normalize_title("BERT: Pre-training of Transformers") == "bert pre training of transformers"
    )
    assert normalize_title("  Mixed   CASE--test  ") == "mixed case test"


def test_shingle_jaccard_threshold():
    a = shingle_set("the quick brown fox jumps over the lazy dog " * 5)
    assert jaccard(a, a) == 1.0
    assert jaccard(a, shingle_set("completely different content about compilers and kernels")) < 0.2
    b = shingle_set(
        "the quick brown fox jumps over the lazy dog " * 4 + "unrelated tail text here " * 3
    )
    assert jaccard(a, b) > 0.3


def test_compare_contracts_synthetic():
    body = (
        "cats sleep sixteen hours a day and purr softly when contented. "
        "domestic cats weigh between four and five kilograms on average. "
        "cats are obligate carnivores and hunt small prey at night."
    )
    other_body = (
        "quarks and gluons interact via the strong force inside hadrons. "
        "quantum chromodynamics predicts asymptotic freedom at high energies. "
        "lattice simulations compute the hadron mass spectrum numerically."
    )
    base = {
        "paper_id": "aaa",
        "title": "A Study of Cats",
        "num_pages": 3,
        "pages": [{"page_no": 1, "blocks": [{"type": "text", "text": body}]}],
        "references": [],
        "meta": {"sha256": "0" * 64, "source_filename": "a.pdf"},
    }
    same = dict(
        base,
        paper_id="bbb",
        title="A Study of Cats v2",
        meta={"sha256": "1" * 64, "source_filename": "a_v2.pdf"},
    )
    other = {
        "paper_id": "ccc",
        "title": "Quantum Chromodynamics",
        "num_pages": 3,
        "pages": [{"page_no": 1, "blocks": [{"type": "text", "text": other_body}]}],
        "references": [],
        "meta": {"sha256": "2" * 64, "source_filename": "c.pdf"},
    }
    assert compare_contracts(base, same)["same_work"] is True
    assert compare_contracts(base, other)["same_work"] is False


def test_suggest_keep_arxiv_version():
    c1 = {
        "paper_id": "p1",
        "title": "T",
        "num_pages": 5,
        "pages": [],
        "references": [],
        "meta": {"sha256": "a" * 64, "source_filename": "1706.03762v1.pdf"},
    }
    c7 = {
        "paper_id": "p7",
        "title": "T",
        "num_pages": 5,
        "pages": [],
        "references": [],
        "meta": {"sha256": "b" * 64, "source_filename": "1706.03762v7.pdf"},
    }
    keep, reason = suggest_keep([c1, c7])
    assert keep == "p7"
    assert "v7" in reason
