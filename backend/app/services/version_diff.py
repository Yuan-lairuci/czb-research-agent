"""同工作版本差异服务（模块一延伸）：章节级差异 + LLM 版本差异总结。

对应评分测试用例 3.1 高级表现：同一工作两个版本先后入库 → 语义去重预警，
用户选择覆盖或保留，且可获得 LLM 版本差异总结。
"""

from __future__ import annotations

from typing import Any

from app.services.pdf_dedup import jaccard, shingle_set, suggest_keep

SECTION_SIM_THRESHOLD = 0.75
_SUMMARY_EXCERPT_CHARS = 240
_SUMMARY_MAX_SECTIONS = 8


def split_sections(contract: dict[str, Any]) -> list[dict[str, str]]:
    """按 heading 块切章节；References/参考文献 及其后内容不计。返回 [{"name","text"}]。"""
    sections: list[dict[str, str]] = []
    name = "(开篇)"
    buf: list[str] = []
    for page in contract.get("pages", []):
        for block in page.get("blocks", []):
            btype = block.get("type")
            text = (block.get("text") or "").strip()
            if btype == "heading":
                low = text.lower().replace(" ", "")
                if low.startswith("references") or low.startswith("参考文献"):
                    if buf:
                        sections.append({"name": name, "text": "\n".join(buf)})
                    return sections
                if buf:
                    sections.append({"name": name, "text": "\n".join(buf)})
                    buf = []
                name = text or name
            elif btype in ("text", "table", "formula") and text:
                buf.append(text)
    if buf:
        sections.append({"name": name, "text": "\n".join(buf)})
    return sections


def _merge_by_name(sections: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """按小写名归并；value 保留首次出现的显示名与合并正文。"""
    merged: dict[str, dict[str, str]] = {}
    for sec in sections:
        key = sec["name"].strip().lower()
        if key in merged:
            merged[key]["text"] += "\n" + sec["text"]
        else:
            merged[key] = {"name": sec["name"].strip(), "text": sec["text"]}
    return merged


def diff_contracts(older: dict[str, Any], newer: dict[str, Any]) -> list[dict[str, Any]]:
    """章节级差异。status ∈ added | removed | modified | unchanged（以 newer 为基准命名）。"""
    old_map = _merge_by_name(split_sections(older))
    new_map = _merge_by_name(split_sections(newer))
    changes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in list(old_map) + [k for k in new_map if k not in old_map]:
        if key in seen:
            continue
        seen.add(key)
        in_old, in_new = key in old_map, key in new_map
        old_entry, new_entry = old_map.get(key), new_map.get(key)
        old_text = old_entry["text"] if old_entry else ""
        new_text = new_entry["text"] if new_entry else ""
        display = (new_entry or old_entry)["name"]
        if in_old and in_new:
            sim = jaccard(shingle_set(old_text), shingle_set(new_text))
            status = "unchanged" if sim >= SECTION_SIM_THRESHOLD else "modified"
        elif in_new:
            status = "added"
        else:
            status = "removed"
        changes.append(
            {
                "name": display,
                "status": status,
                "old_chars": len(old_text),
                "new_chars": len(new_text),
            }
        )
    return changes


async def summarize_version_diff(
    older: dict[str, Any],
    newer: dict[str, Any],
    changes: list[dict[str, Any]],
    version_evidence: str,
    chat: Any | None = None,
) -> str:
    """LLM 版本差异总结（中文要点列表）；chat 可注入以便测试。"""
    if chat is None:
        from app.services.chat_service import chat_service as chat

    notable = [c for c in changes if c["status"] != "unchanged"]
    lines = [
        f"- 章节「{c['name']}」: {c['status']}（{c['old_chars']} → {c['new_chars']} 字符）"
        for c in notable[:_SUMMARY_MAX_SECTIONS]
    ] or ["- 章节结构与内容无实质变化"]
    excerpts: list[str] = []
    new_secs = _merge_by_name(split_sections(newer))
    for c in notable[:_SUMMARY_MAX_SECTIONS]:
        entry = new_secs.get(c["name"].strip().lower())
        text = entry["text"] if entry else ""
        if text:
            excerpts.append(f"[{c['name']}] {text[:_SUMMARY_EXCERPT_CHARS]}")
    user = (
        f"旧版本论文标题：{older.get('title', '')}\n"
        f"新版本论文标题：{newer.get('title', '')}\n"
        f"版本判定依据：{version_evidence}\n"
        f"章节级差异信号：\n" + "\n".join(lines) + "\n"
        f"新版本对应章节节选：\n" + "\n".join(excerpts) +
        "\n请总结两个版本之间最可能的修改（3-6 条中文要点，每条一行，以 - 开头）。"
        "只依据以上信号推断，不确定处用「可能」表述，不要编造具体实验数字。"
    )
    messages = [
        {
            "role": "system",
            "content": "你是科研论文版本分析助手，输出简洁中文要点列表，不输出其他内容。",
        },
        {"role": "user", "content": user},
    ]
    return (await chat.chat(messages, temperature=0.3)).strip()


def decide_older_newer(
    contract_a: dict[str, Any], contract_b: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """返回 (older, newer, version_evidence)；复用 pdf_dedup 的版本判定规则。"""
    keep_id, reason = suggest_keep([contract_a, contract_b])
    if contract_a.get("paper_id") == keep_id:
        return contract_b, contract_a, reason
    return contract_a, contract_b, reason
