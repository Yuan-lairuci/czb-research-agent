"""mask/unmask 往返无损、占位符覆盖、流式还原测试（零网络）。"""
from __future__ import annotations

from app.services.translate_mask import StreamUnmasker, mask, unmask

RICH = (
    "# 标题\n\n"
    "行内代码 `def f(x): return x*2` 与行间公式 $$E=mc^2$$ 以及行内公式 $a_i$。\n"
    "链接 https://example.com/path?q=1 以及数字 3.5km、100MB、37°C、9.8m/s^2。\n"
    "```\nprint('hi')\n```\n"
    "纯数字 42 与负数 -7。\n"
)


def test_mask_roundtrip_lossless() -> None:
    """往返断言：unmask(mask(text)) == text（代码/公式/URL/数字全覆盖）。"""
    masked, mapping = mask(RICH)
    assert unmask(masked, mapping) == RICH


def test_mask_replaces_protected_fragments() -> None:
    """掩码文本不再含反引号/$/URL/受保护数字；占位符与 mapping 一一对应。"""
    masked, mapping = mask(RICH)
    assert "`" not in masked
    assert "$" not in masked
    assert "http" not in masked
    assert "3.5km" not in masked
    assert "42" not in masked
    assert "{{M0}}" in masked
    assert len(mapping) == 11
    assert "3.5km" in mapping
    assert "$$E=mc^2$$" in mapping
    assert "https://example.com/path?q=1" in mapping


def test_unmask_tolerant_spacing_and_unknown_index() -> None:
    """还原容忍占位符空白差异；未知下标原样保留（防御 LLM 改写）。"""
    mapping = ["X", "Y"]
    assert unmask("{{ M0 }}|{{M 1}}|{{M9}}", mapping) == "X|Y|{{M9}}"


def test_stream_unmasker_placeholder_split_across_chunks() -> None:
    """流式还原：占位符被增量块截断也能无损还原（与整段 unmask 等价）。"""
    text = "公式 $E=mc^2$ 与 3.5km、代码 `x=1`。"
    masked, mapping = mask(text)
    unmasker = StreamUnmasker(mapping)
    out: list[str] = []
    for i in range(0, len(masked), 3):  # 每次只喂 3 字符，强制截断占位符
        out.append(unmasker.feed(masked[i : i + 3]))
    out.append(unmasker.flush())
    assert "".join(out) == text


def test_stream_unmasker_literal_brace_passthrough() -> None:
    """非占位符字面 {{ 在流结束后按原样输出，不吞不丢。"""
    unmasker = StreamUnmasker(["X"])
    assert unmasker.feed("a{{b") == "a"
    assert unmasker.flush() == "{{b"
    assert unmasker.feed("{{M0}} tail") == "X tail"
    assert unmasker.flush() == ""
