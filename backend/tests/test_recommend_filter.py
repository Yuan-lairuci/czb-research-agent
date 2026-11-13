"""模块四测试：引用含金量过滤（启发式）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.recommend import (
    DEFAULT_MIN_CONTEXT_CHARS,
    filter_valuable_references,
    is_perfunctory,
)


def test_background_short_context_is_perfunctory():
    """background 意图 + 全部短上下文 → 应被过滤。"""
    assert is_perfunctory(
        ["See also [1].", "Related work."],
        ["background", "background"],
    )


def test_method_intent_kept():
    """方法用途引用不应被过滤（真正使用了被引工作）。"""
    assert not is_perfunctory(["We use their tokenizer."], ["methodology"])


def test_mixed_intent_kept():
    """含非 background 意图（含 null）时保守保留。"""
    assert not is_perfunctory(["short"], ["background", "methodology"])
    assert not is_perfunctory(["short"], ["background", None])


def test_long_context_kept():
    """即使全是 background，有长上下文则保留。"""
    long_ctx = "x" * DEFAULT_MIN_CONTEXT_CHARS
    assert not is_perfunctory([long_ctx], ["background"])
    assert is_perfunctory(["x" * 39], ["background"])


def test_missing_evidence_kept():
    """缺少 contexts/intents 证据时保守保留，不误删。"""
    assert not is_perfunctory([], ["background"])
    assert not is_perfunctory(["anything"], [])
    assert not is_perfunctory([], [])


def test_custom_threshold():
    """阈值可注入：min_context_chars=5 时 4 字符即短。"""
    assert is_perfunctory(["1234"], ["background"], min_context_chars=5)
    assert not is_perfunctory(["12345"], ["background"], min_context_chars=5)


def test_filter_valuable_references_splits():
    """过滤器把引用条目分成 kept / dropped 两组。"""
    refs = [
        {"paper": {"paperId": "a", "title": "Perfunctory"},
         "contexts": ["see also"], "intents": ["background"]},
        {"paper": {"paperId": "b", "title": "Substantive"},
         "contexts": ["we build on the benchmark from [2]"], "intents": ["methodology"]},
    ]
    kept, dropped = filter_valuable_references(refs)
    assert [k["paper"]["title"] for k in kept] == ["Substantive"]
    assert [d["paper"]["title"] for d in dropped] == ["Perfunctory"]
