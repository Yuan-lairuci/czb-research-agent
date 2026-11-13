"""局域引用图谱构建：语料 references 映射 + 引用上下文抽取 + 图持久化。

- 节点 = 语料论文（backend/data/parsed/{paper_id}.json 契约）；
- 边 = A 引用 B（仅语料内边），附 [n] 标记附近句子作为引用上下文；
- 持久化 backend/data/graphs/{graph_id}.json（graph_id 为语料 id 摘要，幂等）。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.graph_models import CitationGraph, GraphEdge, GraphNode
from app.models.paper_models import ARXIV_ID_RE

_MARKER_RE = re.compile(r"\[(\d{1,3}(?:\s*[-–,]\s*\d{1,3})*)\]")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")
_YEAR_PREFIX_LEN = 2
_ARXIV_VERSION_RE = re.compile(r"v\d+$")


class GraphBuildError(ValueError):
    """建图失败的基类异常。"""


class ParsedPaperNotFoundError(GraphBuildError):
    """parsed/{paper_id}.json 不存在。"""


def normalize_title(title: str) -> str:
    """标题归一：小写、去非字母数字（供模糊匹配）。"""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def arxiv_year(arxiv_id: str) -> int | None:
    """由 arxiv id 前缀 YYMM 推断年份（如 1706.x → 2017）。"""
    m = re.match(r"(\d{2})(\d{2})\.", arxiv_id)
    if not m:
        return None
    yy = int(m.group(1))
    if not 1 <= int(m.group(2)) <= 12:
        return None
    return 2000 + yy


def _node_identity(contract: dict[str, Any], meta_map: dict[str, dict[str, Any]] | None):
    """节点元信息：meta_map 优先，其次 source_filename 推断 arxiv_id/year。"""
    pid = contract["paper_id"]
    meta = (meta_map or {}).get(pid, {})
    arxiv_id = meta.get("arxiv_id")
    year = meta.get("year")
    if arxiv_id is None:
        filename = contract.get("meta", {}).get("source_filename", "") or ""
        m = ARXIV_ID_RE.search(filename)
        if m:
            arxiv_id = m.group(0)
    if year is None and arxiv_id:
        year = arxiv_year(arxiv_id)
    return GraphNode(paper_id=pid, title=contract["title"], year=year, arxiv_id=arxiv_id)


def _expand_marker(body: str) -> set[int]:
    """展开标记体：'3' / '1, 2, 5' / '1-3' → index 集合。"""
    nums: set[int] = set()
    for part in re.split(r"\s*,\s*", body):
        rng = re.match(r"(\d{1,3})\s*[-–]\s*(\d{1,3})$", part.strip())
        if rng:
            lo, hi = int(rng.group(1)), int(rng.group(2))
            if lo <= hi <= lo + 50:
                nums.update(range(lo, hi + 1))
            continue
        if part.strip().isdigit():
            nums.add(int(part.strip()))
    return nums


def find_citation_context(contract: dict[str, Any], ref_index: int) -> str:
    """在 citing 论文 blocks 中定位 [ref_index] 标记所在句子；找不到返回空串。"""
    for page in contract.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("type") not in ("text", "heading"):
                continue
            text = block.get("text", "")
            hit_sentence = ""
            # 保护 "et al." 缩写句点，避免误断句
            protected = text.replace("et al.", "et al\x00")
            for sentence in _SENT_SPLIT_RE.split(protected):
                sentence = sentence.replace("\x00", ".")
                for m in _MARKER_RE.finditer(sentence):
                    if ref_index in _expand_marker(m.group(1)):
                        hit_sentence = sentence.strip()
                        break
                if hit_sentence:
                    break
            if hit_sentence:
                return hit_sentence[:300]
    return ""


def _match_target(ref: dict[str, Any], corpus: dict[str, GraphNode]) -> str | None:
    """reference → 语料论文映射：arxiv_id 优先，其次标题归一包含匹配。"""
    ref_arxiv = ref.get("arxiv_id")
    if ref_arxiv:
        ref_arxiv = _ARXIV_VERSION_RE.sub("", ref_arxiv)
        for pid, node in corpus.items():
            if node.arxiv_id and _ARXIV_VERSION_RE.sub("", node.arxiv_id) == ref_arxiv:
                return pid
    ref_title = normalize_title(ref.get("title") or "")
    if ref_title:
        for pid, node in corpus.items():
            norm = normalize_title(node.title)
            if not norm:
                continue
            if norm == ref_title:
                return pid
            if (
                len(norm) >= 12
                and len(ref_title) >= 12
                and (norm in ref_title or ref_title in norm)
            ):
                return pid
    return None


def load_corpus(
    paper_ids: list[str] | None, parsed_dir: Path
) -> dict[str, dict[str, Any]]:
    """加载语料契约；paper_ids 缺省 = parsed/ 下全部。"""
    parsed_dir = Path(parsed_dir)
    if paper_ids is None:
        paper_ids = sorted(p.stem for p in parsed_dir.glob("*.json"))
    contracts: dict[str, dict[str, Any]] = {}
    for pid in paper_ids:
        path = parsed_dir / f"{pid}.json"
        if not path.exists():
            raise ParsedPaperNotFoundError(f"parsed contract for {pid} not found")
        contracts[pid] = json.loads(path.read_text(encoding="utf-8"))
    return contracts


def build_graph(
    paper_ids: list[str] | None = None,
    *,
    parsed_dir: Path,
    graphs_dir: Path,
    meta_map: dict[str, dict[str, Any]] | None = None,
) -> CitationGraph:
    """从语料建局域引用图并持久化，返回 CitationGraph。"""
    contracts = load_corpus(paper_ids, parsed_dir)
    corpus: dict[str, GraphNode] = {
        pid: _node_identity(c, meta_map) for pid, c in contracts.items()
    }

    edges: list[GraphEdge] = []
    seen: set[tuple[str, str]] = set()
    for pid, contract in contracts.items():
        for ref in contract.get("references", []):
            target = _match_target(ref, corpus)
            if target is None or target == pid:
                continue
            if (pid, target) in seen:
                continue
            seen.add((pid, target))
            ref_index = int(ref.get("index", 0))
            edges.append(
                GraphEdge(
                    source=pid,
                    target=target,
                    ref_index=ref_index,
                    context=find_citation_context(contract, ref_index),
                )
            )

    graph_id = hashlib.sha256(",".join(sorted(contracts)).encode()).hexdigest()[:12]
    graph = CitationGraph(
        graph_id=graph_id,
        created_at=datetime.now(UTC).isoformat(),
        nodes=[corpus[pid] for pid in sorted(corpus)],
        edges=edges,
    )
    save_graph(graphs_dir, graph)
    return graph


def save_graph(graphs_dir: Path, graph: CitationGraph) -> Path:
    """图谱 JSON 落盘 backend/data/graphs/{graph_id}.json。"""
    graphs_dir = Path(graphs_dir)
    graphs_dir.mkdir(parents=True, exist_ok=True)
    out = graphs_dir / f"{graph.graph_id}.json"
    out.write_text(
        json.dumps(graph.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def load_graph(graphs_dir: Path, graph_id: str) -> CitationGraph:
    """读取已落盘图谱；不存在抛 ParsedPaperNotFoundError 语义外的 KeyError→GraphBuildError。"""
    path = Path(graphs_dir) / f"{graph_id}.json"
    if not path.exists():
        raise GraphBuildError(f"graph {graph_id} not found")
    return CitationGraph.model_validate(json.loads(path.read_text(encoding="utf-8")))
