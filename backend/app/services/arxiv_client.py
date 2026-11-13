"""arXiv API 客户端（export.arxiv.org/api/query，免费无需 key）。

能力：
- 标题/关键词检索（search_query=all|ti|abs:...）；
- 按 arxiv_id 取元数据（id_list 查询，返回条目 id 自带最新版本号）；
- 下载 PDF（arxiv.org/pdf/{id}）；
- 版本号解析：1706.03762v7 -> ("1706.03762", 7)，无 v 后缀 -> (id, None)。

网络调用集中在 ArxivClient，client 可注入以实现零网络单测。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# 现代 id：4 位.4-5 位；旧式 id：archive/7 位（如 cs/0112017）
_VERSION_RE = re.compile(
    r"^(?P<base>\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v(?P<version>\d+))?$"
)
_BASE_RE = re.compile(r"^\d{4}\.\d{4,5}$")


class ArxivError(RuntimeError):
    """arXiv 请求失败（网络/HTTP 错误）。"""


def parse_version(arxiv_id: str) -> tuple[str, int | None]:
    """解析 arxiv_id 的基号与版本号。

    >>> parse_version("1706.03762v7")
    ('1706.03762', 7)
    >>> parse_version("1706.03762")
    ('1706.03762', None)
    """
    m = _VERSION_RE.match(arxiv_id.strip())
    if m is None:
        raise ValueError(f"invalid arxiv_id: {arxiv_id!r}")
    version = m.group("version")
    return m.group("base"), int(version) if version is not None else None


def is_bare_arxiv_id(value: str) -> bool:
    """判断是否为可交给 S2 的 arXiv 标识（裸 id 或带版本）。"""
    return _VERSION_RE.match(value.strip()) is not None


def _text(entry: ET.Element, tag: str) -> str:
    node = entry.find(f"atom:{tag}", _ATOM_NS)
    return (node.text or "").strip() if node is not None else ""


def _entry_to_meta(entry: ET.Element) -> dict[str, Any]:
    """Atom entry -> 元数据 dict（arxiv_id 含版本号）。"""
    raw_id = _text(entry, "id")
    abs_id = raw_id.rsplit("/", 1)[-1]
    base, version = parse_version(abs_id)
    authors = [
        (node.find("atom:name", _ATOM_NS).text or "").strip()
        for node in entry.findall("atom:author", _ATOM_NS)
        if node.find("atom:name", _ATOM_NS) is not None
    ]
    return {
        "arxiv_id": abs_id,
        "base_id": base,
        "version": version,
        "title": re.sub(r"\s+", " ", _text(entry, "title")),
        "abstract": re.sub(r"\s+", " ", _text(entry, "summary")),
        "published": _text(entry, "published"),
        "authors": authors,
        "pdf_url": ARXIV_PDF_URL.format(arxiv_id=abs_id),
    }


class ArxivClient:
    """arXiv API 客户端；client 可注入（须提供 get(url, params=..., timeout=...)）。"""

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = client if client is not None else httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def _get(self, url: str, params: dict[str, Any]) -> httpx.Response:
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ArxivError(f"arXiv request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ArxivError(f"arXiv HTTP {resp.status_code} for {url}")
        return resp

    def _query(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        resp = self._get(ARXIV_API_URL, params)
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            raise ArxivError(f"arXiv Atom parse error: {exc}") from exc
        return [_entry_to_meta(e) for e in root.findall("atom:entry", _ATOM_NS)]

    def search(self, query: str, max_results: int = 10, field: str = "all") -> list[dict[str, Any]]:
        """关键词/标题检索；field: all|title|abstract（API 记号 ti/abs/all）。"""
        api_field = {"all": "all", "title": "ti", "abstract": "abs"}[field]
        return self._query(
            {
                "search_query": f"{api_field}:\"{query}\"",
                "max_results": max_results,
                "sortBy": "relevance",
            }
        )

    def get_paper(self, arxiv_id: str) -> dict[str, Any] | None:
        """按 arxiv_id 取元数据；不存在返回 None。返回的 arxiv_id 自带最新版本号。"""
        base, _ = parse_version(arxiv_id)
        entries = self._query({"id_list": base})
        return entries[0] if entries else None

    def download_pdf(self, arxiv_id: str) -> bytes:
        """下载 PDF 原始字节（arxiv.org/pdf/{id}）。失败抛 ArxivError。"""
        parse_version(arxiv_id)  # 先做 id 合法性校验
        resp = self._get(ARXIV_PDF_URL.format(arxiv_id=arxiv_id), {})
        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type and not resp.content.startswith(b"%PDF"):
            raise ArxivError(f"arXiv PDF endpoint returned non-PDF content: {content_type!r}")
        return resp.content

    def save_pdf(self, arxiv_id: str, dest_dir: Path | str) -> Path:
        """下载 PDF 并保存到 dest_dir/{arxiv_id}.pdf，返回文件路径。"""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        out_path = dest / f"{arxiv_id.replace('/', '_')}.pdf"
        out_path.write_bytes(self.download_pdf(arxiv_id))
        return out_path

    def close(self) -> None:
        """关闭内部 httpx.Client（仅当由本实例创建时）。"""
        if self._owns_client:
            self._client.close()
