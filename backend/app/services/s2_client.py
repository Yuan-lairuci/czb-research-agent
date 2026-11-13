"""Semantic Scholar Graph API 客户端（api.semanticscholar.org，免费无 key）。

限速与退避：
- 全局限速：相邻请求最小间隔 min_interval 秒（默认 1.0，约 1 请求/秒）；
- 429/5xx 指数退避重试（默认最多 2 次），重试同样受最小间隔约束；
- 404 返回 None（论文不存在），其余 4xx 抛 S2Error。

覆盖端点：论文搜索、按 DOI/arXiv id 查详情（citationCount、
influentialCitationCount、externalIds、openAccessPdf.url）、references 与
citations 列表（含 citation contexts 与 intents: background/method/result）。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

S2_BASE_URL = "https://api.semanticscholar.org/graph/v1"

# 详情字段（含 GitHub 代码链接可能现身的 externalIds / url / openAccessPdf）
PAPER_FIELDS = (
    "title,abstract,year,citationCount,influentialCitationCount,"
    "externalIds,openAccessPdf,url,authors"
)
# 引用条目字段：contexts/intents 用于“引用含金量”过滤
CITE_FIELDS = (
    "title,abstract,year,citationCount,influentialCitationCount,"
    "externalIds,openAccessPdf,url,contexts,intents"
)

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(v\d+)?$")


class S2Error(RuntimeError):
    """Semantic Scholar 请求失败（网络/HTTP 错误，429 重试耗尽）。"""


def normalize_paper_id(paper_id: str) -> str:
    """把裸 arxiv_id / DOI 归一为 S2 可识别的 ID 形态。

    - 1706.03762(v7) -> arxiv:1706.03762(v7)
    - 10.xxxx/yyyy -> DOI:10.xxxx/yyyy
    - 其余（S2 hash）原样返回
    """
    pid = paper_id.strip()
    if _DOI_RE.match(pid):
        return f"DOI:{pid}"
    if _ARXIV_RE.match(pid):
        return f"arxiv:{pid}"
    return pid


class S2Client:
    """S2 Graph API 客户端；client / sleep_fn 可注入以实现零网络单测。"""

    def __init__(
        self,
        client: httpx.Client | None = None,
        min_interval: float = 1.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
        timeout: float = 30.0,
    ) -> None:
        self._client = client if client is not None else httpx.Client(timeout=timeout)
        self._owns_client = client is None
        self._min_interval = min_interval
        self._sleep = sleep_fn
        self._max_retries = max_retries
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        """保证距上次请求至少 min_interval 秒。"""
        if self._min_interval <= 0:
            return
        wait = self._min_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = time.monotonic()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """GET 单个端点；404 -> None；429/5xx 指数退避重试；其余 4xx 抛 S2Error。"""
        url = f"{S2_BASE_URL}{path}"
        last_error = ""
        for attempt in range(self._max_retries + 1):
            self._throttle()
            try:
                resp = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = f"network error: {exc}"
                if attempt < self._max_retries:
                    self._sleep(2.0**attempt)
                    continue
                raise S2Error(f"S2 request failed: {last_error}") from exc
            if resp.status_code == 404:
                return None
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                if attempt < self._max_retries:
                    self._sleep(2.0**attempt)
                    continue
                raise S2Error(f"S2 request failed after retries: {last_error} for {path}")
            if resp.status_code != 200:
                raise S2Error(f"S2 HTTP {resp.status_code} for {path}: {resp.text[:200]}")
            return resp.json()
        raise S2Error(f"S2 request failed: {last_error} for {path}")

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """论文搜索；S2 不可达/失败抛 S2Error（由上层决定降级）。"""
        data = self._get(
            "/paper/search",
            {"query": query, "limit": limit, "fields": PAPER_FIELDS},
        )
        if data is None:
            return []
        return list(data.get("data") or [])

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        """按 S2 id / DOI / arXiv id 查论文详情；不存在返回 None。"""
        return self._get(f"/paper/{normalize_paper_id(paper_id)}", {"fields": PAPER_FIELDS})

    def _list_edges(self, path: str, paper_id: str, limit: int) -> list[dict[str, Any]]:
        data = self._get(
            f"/paper/{normalize_paper_id(paper_id)}{path}",
            {"fields": CITE_FIELDS, "limit": limit},
        )
        if data is None:
            return []
        return list(data.get("data") or [])

    def get_references(self, paper_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """论文的参考文献列表；每条含 contexts/intents（引用含金量过滤的输入）。

        形状：{"paper": {...被引文献元数据...}, "contexts": [str], "intents": [str]}
        """
        out: list[dict[str, Any]] = []
        for edge in self._list_edges("/references", paper_id, limit):
            out.append(
                {
                    "paper": edge.get("citedPaper") or {},
                    "contexts": list(edge.get("contexts") or []),
                    "intents": list(edge.get("intents") or []),
                }
            )
        return out

    def get_citations(self, paper_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """引用该论文的文献列表；每条含 contexts/intents。形状同 get_references。"""
        out: list[dict[str, Any]] = []
        for edge in self._list_edges("/citations", paper_id, limit):
            out.append(
                {
                    "paper": edge.get("citingPaper") or {},
                    "contexts": list(edge.get("contexts") or []),
                    "intents": list(edge.get("intents") or []),
                }
            )
        return out

    def close(self) -> None:
        """关闭内部 httpx.Client（仅当由本实例创建时）。"""
        if self._owns_client:
            self._client.close()
