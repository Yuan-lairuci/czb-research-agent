"""模块五测试：review_gen.py —— 综述结构、[n] 越界引用拒写、模板回退。"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import re

from app.models.graph_models import CitationGraph, GraphNode
from app.services.review_gen import (
    corpus_numbering,
    generate_review,
    sanitize_citations,
)

HEADINGS = ["背景", "方法演进", "代表工作", "现状与挑战"]


def make_graph() -> CitationGraph:
    return CitationGraph(
        graph_id="gtest00000002",
        created_at="2026-01-01T00:00:00+00:00",
        nodes=[
            GraphNode(paper_id="p2", title="Later Paper", year=2020),
            GraphNode(paper_id="p1", title="Earlier Paper", year=2018),
            GraphNode(paper_id="p3", title="Unknown Year Paper", year=None),
        ],
        edges=[],
    )


class ScriptedChat:
    """按脚本返回回答的 chat stub；记录收到的 messages。"""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append(messages)
        return self.answer


def _contracts() -> dict[str, dict[str, Any]]:
    def c(pid: str, text: str) -> dict[str, Any]:
        return {
            "paper_id": pid,
            "title": pid,
            "num_pages": 1,
            "pages": [{"page_no": 1, "blocks": [{"type": "text", "text": text}]}],
            "references": [],
            "meta": {"sha256": "0" * 64, "source_filename": f"{pid}.pdf"},
        }

    return {
        "p1": c("p1", "We propose an early method for the task."),
        "p2": c("p2", "We improve the earlier method with scaling."),
        "p3": c("p3", "We study the general setting."),
    }


def test_review_structure_and_valid_citations():
    """LLM 输出结构解析为 4 小节；合法 [n] 保留并映射语料论文。"""
    script = "\n".join(
        [
            "## 背景",
            "早期方法被提出 [1]。",
            "## 方法演进",
            "后续工作改进了它 [2][1]。",
            "## 代表工作",
            "代表性工作见 [2]。",
            "## 现状与挑战",
            "仍存在挑战 [1]。",
        ]
    )
    stub = ScriptedChat(script)
    graph = make_graph()
    resp = __import__("asyncio").run(
        generate_review(graph, _contracts(), topic="测试主题", llm=stub)
    )
    assert resp.generated_by == "llm"
    assert [s.heading for s in resp.sections] == HEADINGS
    assert resp.dropped_citations == []
    n_map = {r.n: r.paper_id for r in resp.citation_map}
    assert n_map == {1: "p1", 2: "p2", 3: "p3"}
    assert "[1]" in resp.sections[0].content
    assert resp.topic == "测试主题"
    prompt = stub.calls[0][1]["content"]
    assert "只能依据" in prompt
    assert "Earlier Paper" in prompt and "Later Paper" in prompt
    assert "标题依次为：背景" in prompt


def test_review_drops_out_of_range_citation():
    """[99] 越界引用拒写：正文剔除标注 + dropped 登记，合法 [1] 保留。"""
    script = "\n".join(
        [
            "## 背景",
            "不存在的结论 [99] 被编造。",
            "## 方法演进",
            "混合引用 [1, 99] 也要拆分。",
            "## 代表工作",
            "真实结论 [1] 保留。",
            "## 现状与挑战",
            "无引用句子 [98] 结束。",
        ]
    )
    resp = __import__("asyncio").run(
        generate_review(make_graph(), _contracts(), llm=ScriptedChat(script))
    )
    joined = "\n".join(s.content for s in resp.sections)
    assert "[99]" not in joined and "[98]" not in joined
    assert "[1]" in joined
    assert "越界已剔除" in joined
    assert len(resp.dropped_citations) == 3
    assert any("99" in d and "越界" in d for d in resp.dropped_citations)
    assert any("98" in d for d in resp.dropped_citations)


def test_review_template_when_no_llm_or_bad_output():
    """llm=None 或输出无小节 → 模板综述，[n] 全部在语料内。"""
    graph = make_graph()
    resp = __import__("asyncio").run(generate_review(graph, _contracts(), llm=None))
    assert resp.generated_by == "template"
    assert [s.heading for s in resp.sections] == HEADINGS
    joined = "\n".join(s.content for s in resp.sections)
    ns = [int(m) for m in re.findall(r"\[(\d+)\]", joined)]
    assert ns and all(1 <= n <= 3 for n in ns)

    bad = ScriptedChat("完全不含小节标记的自由文本")
    resp2 = __import__("asyncio").run(generate_review(graph, _contracts(), llm=bad))
    assert resp2.generated_by == "template"


def test_sanitize_direct():
    """sanitize_citations：整组越界剔除、混合组保留合法部分。"""
    clean, dropped = sanitize_citations("结论 [42] 可疑，混合 [1, 42]，正常 [2]。", {1, 2})
    assert "[42]" not in clean and "[1]" in clean and "[2]" in clean
    assert len(dropped) == 2
    assert "越界" in dropped[0]


def test_corpus_numbering_order():
    """[n] 编号按年份升序，年未知排最后。"""
    refs = corpus_numbering(make_graph())
    assert [r.paper_id for r in refs] == ["p1", "p2", "p3"]
