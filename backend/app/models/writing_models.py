"""模块六 API 数据模型：学术写作辅助与数据可视化 Copilot。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Style = Literal["plain", "apa", "gbt7714"]
DiagramKind = Literal["tikz", "graphviz", "mermaid"]
ChartType = Literal["line", "bar", "radar", "scatter"]


class OutlineRequest(BaseModel):
    """论文框架请求：研究 idea + 可选自定义小节标题（缺省用标准六节）。"""

    idea: str = Field(min_length=1, description="研究 idea（中文或英文）")
    sections: list[str] | None = Field(default=None, description="自定义小节标题列表")


class OutlineSection(BaseModel):
    """论文框架一节：标题 + 要点列表。"""

    title: str
    points: list[str] = Field(default_factory=list)


class OutlineResponse(BaseModel):
    """论文框架响应：按请求顺序返回小节。"""

    idea: str
    sections: list[OutlineSection]


class ReferenceItem(BaseModel):
    """一条已核实参考文献；内容字段全部来自可核实来源（语料/S2/arXiv）。"""

    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    arxiv_id: str | None = None
    doi: str | None = None
    url: str | None = None
    source: str = Field(description="可核实来源标注，如 corpus:xxx#1 / s2:search / arxiv:search")
    formatted: str = Field(default="", description="按 style 格式化的引用条目文本")


class DroppedReference(BaseModel):
    """核对不上被剔除的 LLM 条目及剔除原因。"""

    title: str | None = None
    arxiv_id: str | None = None
    doi: str | None = None
    reason: str


class ReferencesRequest(BaseModel):
    """参考文献请求：topic 或 paper_ids 至少提供一个。"""

    topic: str | None = None
    paper_ids: list[str] | None = None
    style: Style = "plain"
    limit: int = Field(default=10, ge=1, le=50, description="输出条目上限")

    @model_validator(mode="after")
    def _need_query(self) -> ReferencesRequest:
        """topic 与 paper_ids 至少一个非空。"""
        if not (self.topic and self.topic.strip()) and not self.paper_ids:
            raise ValueError("topic 与 paper_ids 至少提供一个")
        return self


class ReferencesResponse(BaseModel):
    """参考文献响应：references 逐条可核实，dropped 为剔除项及原因。"""

    query: str
    style: Style
    references: list[ReferenceItem]
    dropped: list[DroppedReference]
    sources_used: list[str] = Field(default_factory=list)


class DiagramRequest(BaseModel):
    """拓扑图脚本请求。"""

    kind: DiagramKind
    description: str = Field(min_length=1, description="图形/拓扑的文字描述")


class DiagramResponse(BaseModel):
    """拓扑图脚本响应：可直接渲染的代码 + 使用提示。"""

    kind: DiagramKind
    code: str
    usage_hint: str


class ChartRequest(BaseModel):
    """图表请求：rows 或 csv_text 至少提供一个。"""

    rows: list[dict[str, Any]] | None = None
    csv_text: str | None = None
    chart_type: ChartType
    title: str | None = None

    @model_validator(mode="after")
    def _need_data(self) -> ChartRequest:
        """rows 与 csv_text 至少一个非空。"""
        if not self.rows and not (self.csv_text and self.csv_text.strip()):
            raise ValueError("rows 与 csv_text 至少提供一个")
        return self


class ChartResponse(BaseModel):
    """图表响应：ECharts option JSON + 英文 Figure Caption。"""

    option: dict[str, Any]
    caption: str


class Zh2EnRequest(BaseModel):
    """中文学术文本英译请求。"""

    text: str = Field(min_length=1)


class Zh2EnResponse(BaseModel):
    """英译响应：译文 + 引擎标签（复用模块三 TranslateEngine 契约）。"""

    translated: str
    engine: str
