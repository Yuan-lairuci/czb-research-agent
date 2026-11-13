"""学术综述生成：结构化综述 + [n] 引用标记抗幻觉校验。

- 结构：背景 / 方法演进 / 代表工作 / 现状与挑战；
- 提示词只允许依据提供的论文标题/摘要/正文片段，要求每个论断带 [n] 标记；
- 输出后校验：[n] 必须落在语料编号 1..N 内，越界引用从正文剔除并标注，
  同时在 dropped_citations 中登记；citation_map 给出 [n] → 语料论文映射。
- LLM 走注入的 llm.chat（chat_service），未注入/失败时给模板综述（零网络）。
"""

from __future__ import annotations

import re
from typing import Any

from app.models.graph_models import (
    CitationGraph,
    CitationRef,
    ReviewResponse,
    ReviewSection,
)
from app.services.graph_metrics import LLMClient
from app.services.pdf_parse import body_text_of

_CITE_GROUP_RE = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")
_SECTION_RE = re.compile(r"^#{1,3}\s*(.+?)\s*$", re.MULTILINE)
_SECTION_ORDER = ["背景", "方法演进", "代表工作", "现状与挑战"]


class ReviewError(ValueError):
    """综述生成失败。"""


def corpus_numbering(graph: CitationGraph) -> list[CitationRef]:
    """语料论文 → [n] 编号（按年份升序、同年按 paper_id，年未知排最后）。"""
    nodes = sorted(
        graph.nodes, key=lambda n: (n.year if n.year is not None else 9999, n.paper_id)
    )
    return [
        CitationRef(n=i + 1, paper_id=node.paper_id, title=node.title)
        for i, node in enumerate(nodes)
    ]


def _material_lines(
    graph: CitationGraph, refs: list[CitationRef], contracts: dict[str, dict[str, Any]]
) -> list[str]:
    """提示词资料块：[n] 《标题》(年) + 摘要片段（正文前 300 字）。"""
    by_id = {n.paper_id: n for n in graph.nodes}
    lines: list[str] = []
    for ref in refs:
        node = by_id[ref.paper_id]
        year = f"（{node.year}）" if node.year else ""
        snippet = ""
        contract = contracts.get(ref.paper_id)
        if contract:
            snippet = body_text_of(contract)[:300].replace("\n", " ")
        lines.append(f"[{ref.n}] 《{ref.title}》{year} 摘要片段：{snippet}")
    return lines


def build_review_prompt(
    graph: CitationGraph,
    refs: list[CitationRef],
    contracts: dict[str, dict[str, Any]],
    topic: str | None,
) -> list[dict[str, str]]:
    """组装抗幻觉提示词：只允许依据资料块，论断必须带 [n]。"""
    topic_line = f"主题：{topic}" if topic else "主题：由语料内容自动归纳"
    material = "\n".join(_material_lines(graph, refs, contracts))
    user = (
        f"{topic_line}\n"
        "请基于以下语料撰写一篇中文学术综述。\n"
        "硬性要求：只能依据下面提供的论文标题/摘要/正文片段，禁止编造语料之外的事实；"
        "每一个论断句末必须带引用标记 [n]（n 为下方语料编号），可多个如 [1][3]；"
        f"输出四个小节，每节以 '## ' 开头，标题依次为：{'、'.join(_SECTION_ORDER)}。\n"
        "语料：\n" + material
    )
    return [
        {
            "role": "system",
            "content": "你是严谨的学术综述作者，只依据给定语料写作，引用标记不得超出语料编号。",
        },
        {"role": "user", "content": user},
    ]


def sanitize_citations(text: str, valid_ns: set[int]) -> tuple[str, list[str]]:
    """剔除越界 [n] 并标注；返回 (清理后文本, 剔除标注列表)。"""
    dropped: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        nums = [int(x) for x in re.split(r"\s*,\s*", m.group(1)) if x.strip()]
        valid = [n for n in nums if n in valid_ns]
        bad = [n for n in nums if n not in valid_ns]
        for n in bad:
            dropped.append(f"[{n}] 越界引用（语料编号 1..{max(valid_ns)}），已剔除")
        if not valid:
            return f"〔引用 {'、'.join(str(n) for n in bad)} 越界已剔除〕"
        kept = "".join(f"[{n}]" for n in valid)
        if bad:
            return kept + f"〔引用 {'、'.join(str(n) for n in bad)} 越界已剔除〕"
        return kept

    return _CITE_GROUP_RE.sub(_replace, text), dropped


def parse_sections(text: str) -> list[ReviewSection]:
    """按 '## 标题' 切小节；无小节返回空列表。"""
    matches = list(_SECTION_RE.finditer(text))
    sections: list[ReviewSection] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append(ReviewSection(heading=m.group(1), content=body))
    return sections


def template_review(
    graph: CitationGraph, refs: list[CitationRef], topic: str | None
) -> list[ReviewSection]:
    """无 LLM 时的模板综述：论断仅来自节点元数据，[n] 全部在语料内。"""
    if not refs:
        return [
            ReviewSection(heading=h, content="语料为空，无法生成综述。") for h in _SECTION_ORDER
        ]
    topic_txt = topic or "本领域"
    top3 = "、".join(f"《{r.title}》[{r.n}]" for r in refs[:3])
    years = [n.year for n in graph.nodes if n.year]
    span = f"{min(years)}–{max(years)} 年" if years else "不详年份"
    cited = sorted(refs, key=lambda r: r.n)
    per_paper = "；".join(f"《{r.title}》[{r.n}]" for r in cited)
    return [
        ReviewSection(
            heading="背景",
            content=f"{topic_txt}的研究可回溯至语料所覆盖的 {span}，代表性起点工作包括 {top3}。",
        ),
        ReviewSection(
            heading="方法演进",
            content=f"语料内的方法按时间先后演进，依次为：{per_paper}。",
        ),
        ReviewSection(
            heading="代表工作",
            content=f"语料共收录 {len(refs)} 篇代表工作：{per_paper}。",
        ),
        ReviewSection(
            heading="现状与挑战",
            content=(
                f"当前该方向仍以 {top3} 为核心参照，语料之外的挑战与结论不予置评。"
            ),
        ),
    ]


async def generate_review(
    graph: CitationGraph,
    contracts: dict[str, dict[str, Any]],
    topic: str | None = None,
    llm: LLMClient | None = None,
) -> ReviewResponse:
    """生成综述；LLM 可选，输出强制过 [n] 越界校验。"""
    refs = corpus_numbering(graph)
    valid_ns = {r.n for r in refs}
    generated_by = "template"
    sections: list[ReviewSection] = []
    dropped: list[str] = []

    if llm is not None and refs:
        messages = build_review_prompt(graph, refs, contracts, topic)
        try:
            raw = await llm.chat(messages, temperature=0.3)
        except Exception:  # noqa: BLE001 - LLM 失败回退模板
            raw = ""
        parsed = parse_sections(raw or "")
        if parsed:
            generated_by = "llm"
            for sec in parsed:
                clean, bad = sanitize_citations(sec.content, valid_ns)
                sections.append(ReviewSection(heading=sec.heading, content=clean))
                dropped.extend(bad)

    if not sections:
        sections = template_review(graph, refs, topic)

    return ReviewResponse(
        graph_id=graph.graph_id,
        topic=topic,
        generated_by=generated_by,
        sections=sections,
        citation_map=refs,
        dropped_citations=dropped,
    )
