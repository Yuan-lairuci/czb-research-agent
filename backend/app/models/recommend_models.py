"""模块四数据模型：检索候选、推荐、反馈与版本追踪的请求/响应契约。

source 取值约定：
- "s2"         仅 Semantic Scholar 命中
- "arxiv"      仅 arXiv 命中（S2 可用时）
- "both"       双源命中（按 arxiv_id/DOI 合并）
- "arxiv_only" S2 不可达降级：结果来自 arXiv 单源
"""

from __future__ import annotations

from pydantic import BaseModel, Field

SOURCE_VALUES = ("s2", "arxiv", "both", "arxiv_only")


class Candidate(BaseModel):
    """双源合并后的检索候选条目。"""

    title: str
    abstract: str | None = None
    year: int | None = None
    citation_count: int | None = None
    influential_citation_count: int | None = None
    s2_id: str | None = None
    arxiv_id: str | None = None
    doi: str | None = None
    pdf_url: str | None = None
    github_url: str | None = None
    source: str = Field(description="来源标记：" + "/".join(SOURCE_VALUES))


class SearchRequest(BaseModel):
    """检索请求。"""

    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


class SearchResponse(BaseModel):
    """检索响应；degraded=True 表示 S2 不可达、仅 arXiv 结果。"""

    query: str
    results: list[Candidate]
    degraded: bool = False
    s2_ok: bool = True
    arxiv_ok: bool = True


class FetchRequest(BaseModel):
    """按 arxiv_id 下载 PDF 并入库解析的请求。"""

    arxiv_id: str = Field(min_length=1)


class FetchResponse(BaseModel):
    """入库结果：paper_id 为模块一 sha256 前 12 位。"""

    arxiv_id: str
    paper_id: str | None = None
    title: str | None = None
    num_pages: int = 0
    parse_status: str = Field(description="parsed / failed / duplicate")
    detail: str | None = None


class ByPaperRequest(BaseModel):
    """基于单篇论文的推荐请求。"""

    paper_id: str = Field(min_length=1, description="s2_id / arxiv:<id> / DOI:<doi> / arxiv_id")
    top_k: int = Field(default=10, ge=1, le=50)


class Recommendation(BaseModel):
    """单条推荐，含排序分与理由文本。"""

    title: str
    s2_id: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    citation_count: int | None = None
    influential_citation_count: int | None = None
    github_url: str | None = None
    score: float
    reasons: list[str]
    relation: str = Field(description="与种子论文的关系：reference / citation")


class ByPaperResponse(BaseModel):
    """推荐响应。"""

    paper_id: str
    recommendations: list[Recommendation]
    references_considered: int = 0
    citations_considered: int = 0
    dropped_perfunctory: int = 0


class FeedbackRequest(BaseModel):
    """点赞/点踩反馈；s2_id 与 arxiv_id 至少给一个。"""

    s2_id: str | None = None
    arxiv_id: str | None = None
    like: bool


class FeedbackResponse(BaseModel):
    """反馈落库结果。"""

    stored: bool
    paper_key: str


class TrackAddRequest(BaseModel):
    """加入版本追踪的请求。"""

    arxiv_id: str = Field(min_length=1)


class TrackItem(BaseModel):
    """追踪条目：tracked_version 为本地记录 v_n。"""

    arxiv_id: str
    base_id: str
    title: str | None = None
    tracked_version: int | None = None
    latest_version: int | None = None
    added_at: str
    last_checked: str | None = None


class TrackListResponse(BaseModel):
    """追踪列表响应。"""

    items: list[TrackItem]


class TrackUpdate(BaseModel):
    """一次版本更新：old -> new。"""

    arxiv_id: str
    base_id: str
    title: str | None = None
    old_version: int | None = None
    new_version: int = 0


class TrackRunResponse(BaseModel):
    """追踪巡检响应：updates 为检测到的新版本列表。"""

    checked: int = 0
    updates: list[TrackUpdate] = []
    errors: list[str] = []
