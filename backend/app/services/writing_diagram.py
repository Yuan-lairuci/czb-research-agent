"""学术写作辅助：示意图脚本生成（TikZ / Graphviz / Mermaid）。

LLM 只负责生成代码文本；代码骨架由本模块确定性校验，
非法输出抛 DiagramError，由 API 层映射为 502。
"""
from __future__ import annotations

import re

from app.models.writing_models import DiagramKind, DiagramResponse
from app.services.chat_service import ChatService, ChatServiceError, chat_service

_FENCE_RE = re.compile(r"```[A-Za-z0-9_-]*\s*(.*?)```", re.DOTALL)
_MERMAID_START_RE = re.compile(
    r"^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|"
    r"erDiagram|gantt|pie|mindmap|timeline|journey|gitGraph)\b"
)
_GRAPHVIZ_START_RE = re.compile(r"^(?:strict\s+)?(?:di)?graph\b", re.IGNORECASE)

_USAGE_HINTS: dict[DiagramKind, str] = {
    "tikz": (
        "将代码放入 LaTeX 文档（导言区加载 \\usepackage{tikz}），"
        "用 xelatex 编译；可另存为 figure.tex 后在正文 \\input{figure} 引入。"
    ),
    "graphviz": (
        "将代码保存为 .dot 文件后运行 dot -Tpng figure.dot -o figure.png 渲染，"
        "或粘贴到任意 Graphviz 在线预览器查看。"
    ),
    "mermaid": (
        "将代码放入 Markdown 的 mermaid 代码块即可渲染，"
        "或粘贴到 mermaid.live 预览并导出 PNG/SVG。"
    ),
}

_KIND_INSTRUCTIONS: dict[DiagramKind, str] = {
    "tikz": (
        "输出 TikZ（LaTeX）代码：必须完整包含 \\begin{tikzpicture} 与 \\end{tikzpicture}，"
        "可使用 tikz 常用库（positioning、arrows.meta 等）。"
    ),
    "graphviz": (
        "输出 Graphviz DOT 代码：必须以 graph 或 digraph 声明开头，"
        "并用花括号完整包裹节点与边的定义。"
    ),
    "mermaid": (
        "输出 mermaid 代码：首行必须是图类型声明"
        "（如 graph TD、flowchart LR、sequenceDiagram、classDiagram、erDiagram、gantt 等）。"
    ),
}


class DiagramError(RuntimeError):
    """示意图生成失败（LLM 不可用或输出代码非法）。"""


def extract_code(text: str) -> str:
    """剥离可能存在的 Markdown 代码围栏，返回裸代码文本。"""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    stripped = text.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        if first_nl != -1:
            return stripped[first_nl + 1 :].strip()
    return stripped


def validate_code(kind: DiagramKind, code: str) -> None:
    """按 kind 确定性校验代码骨架，非法时抛 DiagramError。"""
    if not code:
        raise DiagramError("LLM 输出为空代码")
    if kind == "tikz":
        if "\\begin{tikzpicture}" not in code or "\\end{tikzpicture}" not in code:
            raise DiagramError("tikz 代码缺少 begin/end tikzpicture 结构")
        return
    first_line = next((line.strip() for line in code.splitlines() if line.strip()), "")
    if kind == "graphviz":
        if not _GRAPHVIZ_START_RE.match(first_line) or "{" not in code or "}" not in code:
            raise DiagramError("graphviz 代码必须以 graph/digraph 声明开头并包含花括号")
        return
    if not _MERMAID_START_RE.match(first_line):
        raise DiagramError(f"mermaid 代码首行不是合法图类型声明: {first_line!r}")


class DiagramEngine:
    """基于 ChatService 的示意图脚本生成引擎（测试可注入 stub）。"""

    def __init__(self, chat: ChatService | None = None) -> None:
        self._chat = chat or chat_service

    @staticmethod
    def _build_messages(kind: DiagramKind, description: str) -> list[dict[str, str]]:
        system = (
            "你是科研论文的示意图绘制助手。"
            f"{_KIND_INSTRUCTIONS[kind]}"
            "只输出代码本身：不要 Markdown 围栏，不要解释文字。"
        )
        user = f"请为下面的研究内容绘制{kind}示意图：\n{description}"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    async def generate_diagram(self, kind: DiagramKind, description: str) -> DiagramResponse:
        """生成并校验示意图代码，返回代码与用法提示。"""
        messages = self._build_messages(kind, description)
        try:
            text = await self._chat.chat(messages, temperature=0.2)
        except ChatServiceError as exc:
            raise DiagramError(f"生成服务不可用: {exc}") from exc
        code = extract_code(text)
        validate_code(kind, code)
        return DiagramResponse(kind=kind, code=code, usage_hint=_USAGE_HINTS[kind])
