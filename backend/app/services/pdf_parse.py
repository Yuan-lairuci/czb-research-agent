"""PDF 细粒度解析服务（PyMuPDF）：产出严格符合 ParsedPaper 契约的 dict 并落盘。

能力：
- 标题树：字号 / 加粗启发式，heading 块携带 level；
- 表格：page.find_tables()，text 为行列拼接；
- 公式区块：数学符号密度启发式打 formula 标；
- 参考文献：References 节检测、条目切分，解析 index / raw / title（启发式）/ arxiv_id。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

from app.models.paper_models import ARXIV_ID_RE, ParsedPaper, validate_contract

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

_CAPTION_RE = re.compile(r"^(?:fig(?:ure)?\.?|table|algorithm|scheme)\s*\d+", re.IGNORECASE)
_SECTION_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*)|[IVX]+)[\.)]?\s+[A-Z]|"
    r"^(?:abstract|references|bibliography|acknowledg?ments?|appendix)\b",
    re.IGNORECASE,
)
_REF_MARKER_RE = re.compile(r"^\s*\[(\d{1,3})\]\s*")
_REF_MARKER_FALLBACK_RE = re.compile(r"^\s*(\d{1,3})\.\s+[A-Z]")
_MATH_CHARS = set("∑∏∫√≤≥≈≠±×÷∞∂∇→←↔∈∋∀∃∧∨⊤⊥⋅⋆⊗⊕∗†‡|‖⌊⌋⌈⌉∓≈≅≡≪≫αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ")
_LATEX_TOKEN_RE = re.compile(
    r"\\(?:frac|sqrt|sum|int|alpha|beta|gamma|theta|lambda|sigma|cdot|times|leq|geq|neq|approx)"
)
_VENUE_RE = re.compile(
    r"^(?:arxiv|corr|proceedings|proc\.?|journal|conference|advances|neurips|nips|icml|iclr|aaai|acl|emnlp|naacl|ieee|acm|springer)",
    re.IGNORECASE,
)


def sha256_of_file(path: Path) -> str:
    """文件内容 sha256（十六进制）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def paper_id_from_sha(sha256: str) -> str:
    """paper_id = 文件 sha256 前 12 位。"""
    return sha256[:12]


def body_text_of(contract: dict[str, Any]) -> str:
    """从契约 dict 提取正文文本（text/heading 块拼接，空白归一）。"""
    parts: list[str] = []
    for page in contract["pages"]:
        for block in page["blocks"]:
            if block["type"] in ("text", "heading"):
                parts.append(block["text"])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 页面级提取
# ---------------------------------------------------------------------------


def _math_density(text: str) -> float:
    """数学符号密度（含 LaTeX 记号加权）。"""
    if not text:
        return 0.0
    math_count = sum(1 for ch in text if ch in _MATH_CHARS)
    math_count += 3 * len(_LATEX_TOKEN_RE.findall(text))
    return math_count / max(len(text), 1)


def _is_formula(text: str) -> bool:
    """公式区块启发式判定。"""
    stripped = text.strip()
    if not stripped or len(stripped) > 400:
        return False
    if "$" in stripped:
        return True
    if _math_density(stripped) >= 0.10:
        return True
    word_count = len(re.findall(r"[A-Za-z]{2,}", stripped))
    math_count = sum(1 for ch in stripped if ch in _MATH_CHARS)
    return ("=" in stripped) and word_count <= 8 and math_count >= 2


def _block_col(bbox: tuple[float, float, float, float], page_width: float) -> int:
    """粗略列归属：0=左列/整宽，1=右列。"""
    x0, _, x1, _ = bbox
    if x0 >= 0.45 * page_width:
        return 1
    return 0


def _text_block_lines(block: dict[str, Any]) -> tuple[str, float, float]:
    """文本块 -> (拼接文本, 最大字号, 最大加粗字号)。"""
    lines: list[str] = []
    max_size = 0.0
    max_bold_size = 0.0
    for line in block.get("lines", []):
        parts: list[str] = []
        for span in line.get("spans", []):
            text = span.get("text", "")
            if text:
                parts.append(text)
            size = float(span.get("size", 0.0))
            max_size = max(max_size, size)
            flags = int(span.get("flags", 0))
            if flags & 16 or "bold" in span.get("font", "").lower():
                max_bold_size = max(max_bold_size, size)
        line_text = "".join(parts).strip()
        if line_text:
            lines.append(line_text)
    return "\n".join(lines), max_size, max_bold_size


def _classify_heading(text: str, size: float, bold_size: float, body_size: float) -> int | None:
    """判断文本块是否为标题，返回 level（1-3）或 None。"""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines or len(lines) > 2:
        return None
    stripped = re.sub(r"\s+", " ", text).strip()
    if len(stripped) > 160:
        return None
    if re.match(r"^arXiv:\d{4}\.\d{4,5}", stripped):
        return None
    numbered = re.match(r"^(\d+(?:\.\d+)*)[\.)]?\s+[A-Z]", stripped)
    known = re.match(
        r"^(?:abstract|references|bibliography|acknowledg?ments?|appendix)\b",
        stripped,
        re.IGNORECASE,
    )
    if numbered:
        return min(len(numbered.group(1).split(".")), 3)
    ratio = size / body_size if body_size else 1.0
    bold_boost = bold_size >= body_size * 0.98
    if known and len(stripped) < 40:
        return 1
    if ratio >= 1.30 and not stripped.endswith("."):
        return 1
    if ratio >= 1.12 and not stripped.endswith("."):
        return 2
    if (
        ((bold_boost and ratio >= 1.0) or ratio >= 1.05)
        and not stripped.endswith(".")
        and len(stripped) < 60
    ):
        return 3
    return None


def _guess_ref_title(raw: str) -> str | None:
    """参考文献标题启发式：优先引号内文本，其次作者后的首个句子。"""
    quoted = re.search(r'["“](.+?)["”]', raw)
    if quoted:
        candidate = quoted.group(1).strip()
        if len(candidate.split()) >= 2:
            return candidate
    for chunk in re.split(r"\.\s+", raw):
        candidate = chunk.strip().rstrip(".")
        words = candidate.split()
        if len(words) < 3 or candidate[:1].islower():
            continue
        if _VENUE_RE.match(candidate) or re.match(r"^\d{4}$", candidate):
            continue
        if re.match(r"^\[?\d{1,3}\]?$", candidate):
            continue
        return candidate
    return None


def _split_references(lines: list[str]) -> list[dict[str, Any]]:
    """参考文献条目切分与字段解析。"""
    entries: list[list[str]] = []
    current: list[str] = []
    marker_mode = False
    for line in lines:
        match = _REF_MARKER_RE.match(line)
        fallback = None if match else _REF_MARKER_FALLBACK_RE.match(line)
        if match or fallback:
            if current:
                entries.append(current)
            current = [line]
            marker_mode = True
        elif marker_mode:
            current.append(line)
    if current:
        entries.append(current)

    refs: list[dict[str, Any]] = []
    for entry_lines in entries:
        raw = " ".join(line.strip() for line in entry_lines)
        raw = re.sub(r"\s+", " ", raw).strip()
        if len(raw) < 15:
            continue
        match = _REF_MARKER_RE.match(entry_lines[0]) or _REF_MARKER_FALLBACK_RE.match(
            entry_lines[0]
        )
        index = int(match.group(1)) if match else len(refs) + 1
        arxiv = ARXIV_ID_RE.search(raw)
        refs.append(
            {
                "index": index,
                "raw": raw,
                "title": _guess_ref_title(raw),
                "arxiv_id": arxiv.group(0) if arxiv else None,
            }
        )
    return refs


# ---------------------------------------------------------------------------
# 文档级解析
# ---------------------------------------------------------------------------


def _body_font_size(doc: pymupdf.Document, sample_pages: int = 10) -> float:
    """正文主字号（按字符数加权的众数，取样前若干页）。"""
    counter: Counter[float] = Counter()
    for page_no in range(min(doc.page_count, sample_pages)):
        data = doc[page_no].get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if text.strip():
                        counter[round(float(span.get("size", 0.0)), 1)] += len(text.strip())
    if not counter:
        return 10.0
    return counter.most_common(1)[0][0]


def _extract_title(doc: pymupdf.Document, body_size: float) -> str:
    """标题启发式：首页顶部最大字号连续行。"""
    page = doc[0]
    height = page.rect.height
    data = page.get_text("dict")
    lines: list[tuple[float, float, str]] = []  # (y0, size, text)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text:
                continue
            size = max((float(span.get("size", 0.0)) for span in spans), default=0.0)
            y0 = float(line["bbox"][1])
            if y0 < 0.35 * height:
                if re.match(r"^arXiv:\d{4}\.\d{4,5}", text) or "@" in text:
                    continue
                lines.append((y0, size, text))
    if not lines:
        meta_title = (doc.metadata or {}).get("title") or ""
        return meta_title.strip() if meta_title.strip() and meta_title.lower() != "untitled" else ""
    lines.sort(key=lambda item: item[0])
    max_size = max(size for _, size, _ in lines)
    picked = [text for _, size, text in lines if size >= 0.9 * max_size]
    title = " ".join(picked)
    title = re.sub(r"\s+", " ", title).strip()
    if title:
        return title
    return lines[0][2]


def parse_pdf(pdf_path: Path, *, source_filename: str | None = None) -> dict[str, Any]:
    """解析 PDF，返回严格符合 ParsedPaper 契约的 dict。"""
    pdf_path = Path(pdf_path)
    source = source_filename or pdf_path.name
    sha256 = sha256_of_file(pdf_path)
    paper_id = paper_id_from_sha(sha256)

    doc = pymupdf.open(pdf_path)
    try:
        body_size = _body_font_size(doc)
        title = _extract_title(doc, body_size)

        pages: list[dict[str, Any]] = []
        ref_lines: list[str] = []
        ref_started = False
        ref_stopped = False

        for page_no in range(doc.page_count):
            page = doc[page_no]
            width = page.rect.width
            items: list[
                tuple[float, int, dict[str, Any]]
            ] = []  # (y_center, 0=text/1=table/2=figure, block)

            tables = []
            try:
                tables = page.find_tables().tables
            except Exception:  # noqa: BLE001 - 表格提取尽力而为
                tables = []
            table_bboxes: list[tuple[float, float, float, float]] = []
            for table in tables:
                bbox = tuple(table.bbox)
                table_bboxes.append(bbox)  # type: ignore[arg-type]
                rows = table.extract()
                row_texts = []
                for row in rows:
                    cells = [re.sub(r"\s+", " ", (cell or "")).strip() for cell in row]
                    if any(cells):
                        row_texts.append(" | ".join(cells))
                text = "\n".join(row_texts)
                items.append(
                    ((bbox[1] + bbox[3]) / 2, 1, {"type": "table", "text": text, "_bbox": bbox})
                )

            data = page.get_text("dict")
            text_items: list[tuple[float, int, dict[str, Any]]] = []
            for block in data.get("blocks", []):
                if block.get("type") != 0:
                    continue
                bbox = tuple(block["bbox"])
                if any(
                    bx0 <= (bbox[0] + bbox[2]) / 2 <= bx1 and by0 <= (bbox[1] + bbox[3]) / 2 <= by1
                    for bx0, by0, bx1, by1 in table_bboxes
                ):
                    continue  # 表格内单元格文本不再重复计入
                text, max_size, max_bold = _text_block_lines(block)
                if not text:
                    continue
                level = _classify_heading(text, max_size, max_bold, body_size)
                if level is not None:
                    item = {
                        "type": "heading",
                        "text": re.sub(r"\s+", " ", text).strip(),
                        "level": level,
                    }
                elif _is_formula(text):
                    item = {"type": "formula", "text": text}
                else:
                    item = {"type": "text", "text": text}
                text_items.append((float(bbox[1]), 0, item))

            # 图片 -> figure 块（附图注文本）
            captions = [
                item for item in text_items if _CAPTION_RE.match(str(item[2].get("text", "")))
            ]
            figure_items: list[tuple[float, int, dict[str, Any]]] = []
            for image in page.get_images(full=True):
                xref = image[0]
                try:
                    rects = page.get_image_rects(xref)
                except Exception:  # noqa: BLE001
                    continue
                for rect in rects:
                    if rect.width * rect.height < 0.005 * (width * page.rect.height):
                        continue  # 忽略图标/装饰小图
                    caption_text = ""
                    best_dist = float("inf")
                    for cap in list(captions):
                        cap_y = cap[0]
                        dist = abs(cap_y - rect.y0)
                        if dist < best_dist and dist < 0.25 * page.rect.height:
                            best_dist = dist
                            caption_text = str(cap[2].get("text", ""))
                            captions.remove(cap)
                    figure_items.append(
                        (
                            ((rect.y0 + rect.y1) / 2),
                            2,
                            {
                                "type": "figure",
                                "text": caption_text,
                                "_bbox": (rect.x0, rect.y0, rect.x1, rect.y1),
                            },
                        )
                    )

            # References 节检测
            if not ref_started:
                for _, _, item in text_items:
                    if item["type"] == "heading" and re.match(
                        r"^(?:references|bibliography)\b", item["text"], re.IGNORECASE
                    ):
                        ref_started = True
                        break
            elif not ref_stopped:
                for _, _, item in text_items:
                    if (
                        item["type"] == "heading"
                        and re.match(r"^(?:appendix|[A-Z][\s.]|[1-9][\.)]?\s)", item["text"])
                        and not re.match(
                            r"^(?:references|bibliography)\b", item["text"], re.IGNORECASE
                        )
                    ):
                        ref_stopped = True
                        break

            items.extend(text_items)
            items.extend(figure_items)
            items.sort(key=lambda entry: (entry[0], entry[1]))

            page_blocks: list[dict[str, Any]] = []
            for _, _, item in items:
                block_out: dict[str, Any] = {"type": item["type"], "text": item["text"]}
                if item["type"] == "heading":
                    block_out["level"] = item["level"]
                page_blocks.append(block_out)
                if ref_started and not ref_stopped and item["type"] in ("text", "heading"):
                    if item["type"] == "text" or not re.match(
                        r"^(?:references|bibliography)\b", item["text"], re.IGNORECASE
                    ):
                        ref_lines.extend(str(item["text"]).split("\n"))
            pages.append({"page_no": page_no + 1, "blocks": page_blocks})

        references = _split_references(ref_lines) if ref_started else []
        contract = ParsedPaper(
            paper_id=paper_id,
            title=title,
            num_pages=doc.page_count,
            pages=[{"page_no": p["page_no"], "blocks": p["blocks"]} for p in pages],  # type: ignore[arg-type]
            references=references,  # type: ignore[arg-type]
            meta={"sha256": sha256, "source_filename": source},  # type: ignore[arg-type]
        ).to_contract()
    finally:
        doc.close()

    errors = validate_contract(contract)
    if errors:
        raise ValueError(f"parsed contract invalid: {errors[:3]}")
    return contract


def save_parsed(contract: dict[str, Any], parsed_dir: Path) -> Path:
    """契约 JSON 落盘 backend/data/parsed/{paper_id}.json，返回文件路径。"""
    parsed_dir = Path(parsed_dir)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    out_path = parsed_dir / f"{contract['paper_id']}.json"
    out_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def load_parsed(parsed_dir: Path, paper_id: str) -> dict[str, Any] | None:
    """读取已落盘的契约 JSON。"""
    path = Path(parsed_dir) / f"{paper_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
