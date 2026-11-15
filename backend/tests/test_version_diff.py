"""version_diff 服务契约测试（不触真实 LLM）。"""

from __future__ import annotations

import asyncio

from app.services.version_diff import (
    decide_older_newer,
    diff_contracts,
    split_sections,
    summarize_version_diff,
)


def make_contract(paper_id: str, filename: str, secs: list[tuple[str | None, list[str]]]) -> dict:
    blocks: list[dict] = []
    for name, texts in secs:
        if name:
            blocks.append({"type": "heading", "text": name, "level": 1})
        for t in texts:
            blocks.append({"type": "text", "text": t})
    return {
        "paper_id": paper_id,
        "title": "Attention Is All You Need",
        "num_pages": 1,
        "pages": [{"page_no": 0, "blocks": blocks}],
        "references": [],
        "meta": {"sha256": paper_id * 4, "source_filename": filename},
    }


A_V7 = ("aaaaaaaaaaaa", "1706.03762v7.pdf")
B_V1 = ("bbbbbbbbbbbb", "1706.03762v1.pdf")


def test_split_sections_stops_at_references() -> None:
    c = make_contract("cccccccccccc", "x.pdf", [
        ("1 Introduction", ["alpha beta gamma delta epsilon"]),
        ("References", ["[1] Some paper"]),
        ("Appendix", ["should not appear"]),
    ])
    names = [s["name"] for s in split_sections(c)]
    assert names == ["1 Introduction"]
    assert all("should not appear" not in s["text"] for s in split_sections(c))


def test_diff_statuses() -> None:
    older = make_contract(B_V1[0], B_V1[1], [
        ("1 Introduction", ["alpha beta gamma delta epsilon"]),
        ("3 Experiments", ["zeta eta theta iota kappa"]),
    ])
    newer = make_contract(A_V7[0], A_V7[1], [
        ("1 Introduction", ["alpha beta gamma delta epsilon"]),
        ("2 Method", ["one two three four five"]),
    ])
    by_name = {c["name"]: c["status"] for c in diff_contracts(older, newer)}
    assert by_name == {
        "1 Introduction": "unchanged",
        "3 Experiments": "removed",
        "2 Method": "added",
    }


def test_diff_modified_detection() -> None:
    older = make_contract(B_V1[0], B_V1[1], [("1 Introduction", ["alpha beta gamma delta epsilon"])])
    newer = make_contract(A_V7[0], A_V7[1], [("1 Introduction", ["完全不同的中文正文用于触发修改判定"])])
    (change,) = diff_contracts(older, newer)
    assert change["status"] == "modified"


def test_decide_older_newer_by_arxiv_version() -> None:
    a = make_contract(*A_V7, [("1 Introduction", ["same body text here for both"])])
    b = make_contract(*B_V1, [("1 Introduction", ["same body text here for both"])])
    older, newer, evidence = decide_older_newer(a, b)
    assert older["paper_id"] == B_V1[0]
    assert newer["paper_id"] == A_V7[0]
    assert "v7" in evidence and "v1" in evidence


class FakeChat:
    def __init__(self) -> None:
        self.last_messages: list[dict] | None = None

    async def chat(self, messages: list[dict], **kwargs: object) -> str:
        self.last_messages = messages
        return "  - 要点一\n- 要点二  "


def test_summarize_uses_llm_and_strips() -> None:
    older = make_contract(B_V1[0], B_V1[1], [("3 Experiments", ["zeta eta theta iota kappa"])])
    newer = make_contract(A_V7[0], A_V7[1], [("2 Method", ["one two three four five"])])
    changes = diff_contracts(older, newer)
    fake = FakeChat()
    out = asyncio.run(summarize_version_diff(older, newer, changes, "arXiv 版本 v7 > v1", chat=fake))
    assert out == "- 要点一\n- 要点二"
    assert fake.last_messages is not None
    user_msg = fake.last_messages[-1]["content"]
    assert "arXiv 版本 v7 > v1" in user_msg
    assert "2 Method" in user_msg
    assert "3 Experiments" in user_msg


def test_summarize_unchanged_sections_excluded() -> None:
    same = [("1 Introduction", ["alpha beta gamma delta epsilon"])]
    older = make_contract(B_V1[0], B_V1[1], same)
    newer = make_contract(A_V7[0], A_V7[1], same)
    changes = diff_contracts(older, newer)
    fake = FakeChat()
    out = asyncio.run(summarize_version_diff(older, newer, changes, "arXiv 版本 v7 > v1", chat=fake))
    assert out == "- 要点一\n- 要点二"
    user_msg = fake.last_messages[-1]["content"]
    assert "无实质变化" in user_msg
    assert "[1 Introduction]" not in user_msg
