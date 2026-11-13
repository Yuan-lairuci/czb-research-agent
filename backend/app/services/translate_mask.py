"""翻译保护层（mask/unmask）：翻译前把不可翻译片段替换为占位符，翻译后无损还原。

保护对象与掩码优先级（单趟正则扫描，备选分支顺序即优先级，单趟保证占位符里的
数字不会被二次匹配）：
1. 三反引号围栏代码块；2. 单反引号行内代码；3. $$...$$ 行间公式；4. $...$ 行内公式；
5. http(s) URL；6. 纯数字与紧贴单位（3.5km / 100MB / 37°C / 9.8m/s^2，纯数字亦保护）。

占位符格式：{{M<n>}}（n 为 mapping 下标，从 0 起）。还原正则容忍空白差异
（{{ M0 }} 亦还原）；未知下标原样保留（防御 LLM 改写占位符）。已知边界：原文中
形如 {{M0}} 的字面量会被误当作占位符还原，学术文本几乎不出现，接受该取舍。

往返保证：unmask(mask(text)) == text 恒成立（单趟替换不递归重扫还原内容），
由 tests/test_translate_mask.py 做往返断言。
"""
from __future__ import annotations

import re

# ---- 掩码正则（按优先级排列在 _COMBINED_RE 的备选分支中）----
# 围栏代码块：三反引号包裹，允许未正常闭合（吞到行尾/文末）。
_FENCED_CODE = r"```[\s\S]*?(?:```|$)"
# 行内代码：单反引号包裹，不含反引号与换行。
_INLINE_CODE = r"`[^`\n]+`"
# 行间公式：$$...$$。
_DISPLAY_MATH = r"\$\$[\s\S]*?(?:\$\$|$)"
# 行内公式：$...$，不含 $ 与换行。
_INLINE_MATH = r"\$[^$\n]+\$"
# URL：http(s) 开头，结尾字符排除标点，避免吞掉句末逗号/句号。
_URL = r"https?://[^\s<>()\[\]{}\"'`]*[^\s<>()\[\]{}\"'`.,;:!?]"
# 纯数字与紧贴单位：3.5km / 100MB / 37°C / 50% / 9.8m/s^2 / 42 / -7。
# 负向后顾排除词内数字（COVID-19 的 19、H1N1 不单独命中）。
_NUMBER = (
    r"(?<![\w.\-])[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
    r"(?:[A-Za-zµμ%Ω°][A-Za-zµμ%Ω°]*(?:\^\{?\d+\}?)?(?:/[A-Za-z0-9²³^]+)*)?"
)

_COMBINED_RE = re.compile(
    f"(?P<fenced>{_FENCED_CODE})"
    f"|(?P<inline_code>{_INLINE_CODE})"
    f"|(?P<display_math>{_DISPLAY_MATH})"
    f"|(?P<inline_math>{_INLINE_MATH})"
    f"|(?P<url>{_URL})"
    f"|(?P<number>{_NUMBER})"
)

# 完整占位符（容忍空白差异）。
_PLACEHOLDER_RE = re.compile(r"\{\{\s*M\s*(\d+)\s*\}\}")


def _restore(text: str, mapping: list[str]) -> str:
    """把 text 中的完整占位符替换为 mapping 原文；未知下标原样保留。"""

    def _sub(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        return mapping[idx] if 0 <= idx < len(mapping) else m.group(0)

    return _PLACEHOLDER_RE.sub(_sub, text)


def mask(text: str) -> tuple[str, list[str]]:
    """把受保护片段替换为 {{M<n>}} 占位符，返回 (掩码文本, mapping)。

    mapping[i] 是第 i 个占位符对应的原文片段，unmask 据此无损还原。
    """
    mapping: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        mapping.append(m.group(0))
        return "{{M" + str(len(mapping) - 1) + "}}"

    return _COMBINED_RE.sub(_sub, text), mapping


def unmask(text: str, mapping: list[str]) -> str:
    """把占位符还原为原文片段（单趟替换，不递归重扫，往返无损）。"""
    return _restore(text, mapping)


class StreamUnmasker:
    """流式占位符还原器：LLM 增量块里占位符可能被截断在两块之间。

    用法：feed(chunk) 每收到一块调用一次，返回可安全输出的还原文本；
    结束后调用 flush() 取回尾部残留。内部总是扣住“可能是未完成占位符”的
    尾巴：最后一个未闭合的 {{ 之后的后缀，或结尾单个可能补全为 {{ 的大括号，
    直到它变完整或流结束。
    """

    def __init__(self, mapping: list[str]) -> None:
        self.mapping = mapping
        self._buf = ""

    def feed(self, chunk: str) -> str:
        """吸收一块增量，返回当前可安全输出的还原文本（可能为空串）。"""
        self._buf += chunk
        cut: int | None = None
        idx = self._buf.rfind("{{")
        if idx != -1 and "}}" not in self._buf[idx + 2 :]:
            cut = idx  # 最后一个 {{ 尚未闭合，整体扣住待补全
        elif self._buf.endswith("{"):
            cut = len(self._buf) - 1  # 结尾单 { 可能补全为 {{
        if cut is not None:
            safe, self._buf = self._buf[:cut], self._buf[cut:]
        else:
            safe, self._buf = self._buf, ""
        return _restore(safe, self.mapping)

    def flush(self) -> str:
        """流结束：还原并吐出尾部残留（残缺占位符按字面输出）。"""
        out, self._buf = self._buf, ""
        return _restore(out, self.mapping)
