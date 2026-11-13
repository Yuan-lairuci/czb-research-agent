"""学术写作辅助：ECharts 图表 option 构建与英文 Figure Caption 生成。

option 由本模块确定性构建（不经过 LLM），保证 schema 正确；
LLM 只提炼英文图注，失败或输出为空时降级为确定性图注（caption 永不为空）。
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.models.writing_models import ChartResponse, ChartType
from app.services.chat_service import ChatService, ChatServiceError, chat_service


class ChartError(RuntimeError):
    """图表数据非法或 option 构建失败。"""


def _to_float(value: Any) -> float | None:
    """空值返回 None，其余尽量转 float；转换失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_numeric(values: list[Any]) -> bool:
    """整列至少一个非空值且所有非空值均可转 float，则视为数值列。"""
    seen = False
    for value in values:
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if _to_float(value) is None:
            return False
        seen = True
    return seen


def parse_csv_rows(csv_text: str) -> list[dict[str, Any]]:
    """解析 CSV（首行表头）为行字典列表。"""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = [dict(row) for row in reader]
    if not rows:
        raise ChartError("csv_text 解析结果为空")
    return rows


def normalize_rows(
    rows: list[dict[str, Any]] | None,
    csv_text: str | None,
) -> list[dict[str, Any]]:
    """rows 与 csv_text 至少一个非空，返回行字典列表。"""
    if rows:
        data = [dict(row) for row in rows]
    elif csv_text:
        data = parse_csv_rows(csv_text)
    else:
        raise ChartError("rows 与 csv_text 至少提供一个")
    if not data:
        raise ChartError("图表数据为空")
    return data


def _columns(rows: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    """返回 (全部列, 维度列, 数值列)，列序按首见顺序。"""
    ordered: list[str] = []
    for row in rows:
        for key in row:
            if key not in ordered:
                ordered.append(key)
    numeric = [col for col in ordered if _is_numeric([row.get(col) for row in rows])]
    dims = [col for col in ordered if col not in numeric]
    return ordered, dims, numeric


def _line_bar_option(
    rows: list[dict[str, Any]],
    chart_type: ChartType,
    title: str | None,
) -> dict[str, Any]:
    ordered, dims, numeric = _columns(rows)
    category_col = dims[0] if dims else ordered[0]
    series_cols = [col for col in numeric if col != category_col]
    if not series_cols:
        raise ChartError(f"数值列不足：除类别列 {category_col} 外没有数值列")
    categories = [str(row.get(category_col, "")) for row in rows]
    series: list[dict[str, Any]] = []
    for col in series_cols:
        values = [_to_float(row.get(col)) for row in rows]
        clean = [v for v in values if v is not None]
        if not clean:
            raise ChartError(f"数值列 {col} 无可用数据")
        series.append({"name": col, "type": chart_type, "data": values})
    return {
        "title": {"text": title or ""},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": [s["name"] for s in series]},
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value"},
        "series": series,
    }


def _radar_option(rows: list[dict[str, Any]], title: str | None) -> dict[str, Any]:
    _, dims, numeric = _columns(rows)
    if not numeric:
        raise ChartError("雷达图需要至少一列数值列")
    indicator: list[dict[str, Any]] = []
    for col in numeric:
        values = [v for v in (_to_float(row.get(col)) for row in rows) if v is not None]
        max_value = max(values) if values else 1.0
        indicator.append({"name": col, "max": max(max_value, 1.0)})
    name_col = dims[0] if dims else None
    data: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        name = str(row.get(name_col, "")) if name_col else f"row{i + 1}"
        values = []
        for col in numeric:
            v = _to_float(row.get(col))
            values.append(v if v is not None else 0.0)
        data.append({"name": name, "value": values})
    return {
        "title": {"text": title or ""},
        "tooltip": {},
        "legend": {"data": [d["name"] for d in data]},
        "radar": {"indicator": indicator},
        "series": [{"type": "radar", "data": data}],
    }


def _scatter_option(rows: list[dict[str, Any]], title: str | None) -> dict[str, Any]:
    _, _, numeric = _columns(rows)
    if len(numeric) < 2:
        raise ChartError("散点图需要至少两列数值列")
    x_col, y_col = numeric[0], numeric[1]
    points: list[list[float]] = []
    for row in rows:
        x = _to_float(row.get(x_col))
        y = _to_float(row.get(y_col))
        if x is not None and y is not None:
            points.append([x, y])
    return {
        "title": {"text": title or ""},
        "tooltip": {"trigger": "item"},
        "legend": {"data": [f"{y_col} vs {x_col}"]},
        "xAxis": {"type": "value", "name": x_col},
        "yAxis": {"type": "value", "name": y_col},
        "series": [{"type": "scatter", "name": f"{y_col} vs {x_col}", "data": points}],
    }


def build_option(
    rows: list[dict[str, Any]],
    chart_type: ChartType,
    title: str | None = None,
) -> dict[str, Any]:
    """确定性构建 ECharts option。"""
    if chart_type == "radar":
        return _radar_option(rows, title)
    if chart_type == "scatter":
        return _scatter_option(rows, title)
    return _line_bar_option(rows, chart_type, title)


def check_option_schema(option: Any, chart_type: ChartType) -> list[str]:
    """校验 option 结构，返回问题列表（空列表 = 合法）。"""
    if not isinstance(option, dict):
        return ["option 不是对象"]
    problems: list[str] = []
    series = option.get("series")
    if not isinstance(series, list) or not series:
        problems.append("series 缺失或为空")
        series = []
    if "tooltip" not in option:
        problems.append("tooltip 缺失")
    if chart_type in ("line", "bar"):
        x_axis = option.get("xAxis")
        y_axis = option.get("yAxis")
        if not isinstance(x_axis, dict) or x_axis.get("type") != "category":
            problems.append("xAxis 必须是 category 类型")
        if not isinstance(y_axis, dict) or y_axis.get("type") != "value":
            problems.append("yAxis 必须是 value 类型")
        if "legend" not in option:
            problems.append("legend 缺失")
        for i, item in enumerate(series):
            if item.get("type") != chart_type:
                problems.append(f"series[{i}].type 应为 {chart_type}")
            if "data" not in item:
                problems.append(f"series[{i}].data 缺失")
    elif chart_type == "radar":
        radar = option.get("radar")
        if not isinstance(radar, dict) or not radar.get("indicator"):
            problems.append("radar.indicator 缺失")
        else:
            for i, ind in enumerate(radar["indicator"]):
                if not isinstance(ind, dict) or "name" not in ind or "max" not in ind:
                    problems.append(f"radar.indicator[{i}] 缺少 name/max")
        for i, item in enumerate(series):
            if item.get("type") != "radar":
                problems.append(f"series[{i}].type 应为 radar")
            if not item.get("data"):
                problems.append(f"series[{i}].data 缺失")
    else:
        x_axis = option.get("xAxis")
        y_axis = option.get("yAxis")
        if not isinstance(x_axis, dict) or x_axis.get("type") != "value":
            problems.append("xAxis 必须是 value 类型")
        if not isinstance(y_axis, dict) or y_axis.get("type") != "value":
            problems.append("yAxis 必须是 value 类型")
        for i, item in enumerate(series):
            if item.get("type") != "scatter":
                problems.append(f"series[{i}].type 应为 scatter")
            for j, point in enumerate(item.get("data") or []):
                if not (isinstance(point, list) and len(point) == 2):
                    problems.append(f"series[{i}].data[{j}] 必须是 [x, y] 数值对")
                    break
    return problems


_FALLBACK_KIND_LABEL: dict[ChartType, str] = {
    "line": "line chart",
    "bar": "bar chart",
    "radar": "radar chart",
    "scatter": "scatter plot",
}


def fallback_caption(chart_type: ChartType, title: str | None) -> str:
    """确定性降级图注，永不为空。"""
    base = title or _FALLBACK_KIND_LABEL[chart_type]
    return f"Figure 1: {base}"


class ChartEngine:
    """基于 ChatService 的图表构建引擎（测试可注入 stub）。"""

    def __init__(self, chat: ChatService | None = None) -> None:
        self._chat = chat or chat_service

    @staticmethod
    def _build_messages(
        rows: list[dict[str, Any]],
        chart_type: ChartType,
        title: str | None,
    ) -> list[dict[str, str]]:
        system = (
            "你是科研论文的图表助手。请为给定图表提炼一句英文学术 Figure Caption，"
            "以 Figure 1: 开头，只输出这一句，不要其它文字。"
        )
        sample = rows[:5]
        user = (
            f"图表类型: {chart_type}\n标题: {title or '(无)'}\n"
            f"数据样本: {json.dumps(sample, ensure_ascii=False, default=str)}"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    async def _caption(
        self,
        rows: list[dict[str, Any]],
        chart_type: ChartType,
        title: str | None,
    ) -> str:
        try:
            text = await self._chat.chat(
                self._build_messages(rows, chart_type, title), temperature=0.3
            )
        except (ChatServiceError, OSError):
            return fallback_caption(chart_type, title)
        caption = text.strip() if isinstance(text, str) else ""
        return caption or fallback_caption(chart_type, title)

    async def build_chart(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        csv_text: str | None = None,
        chart_type: ChartType = "line",
        title: str | None = None,
    ) -> ChartResponse:
        """构建 ECharts option 并生成英文 Figure Caption。"""
        data = normalize_rows(rows, csv_text)
        option = build_option(data, chart_type, title)
        problems = check_option_schema(option, chart_type)
        if problems:
            raise ChartError("option 构建异常: " + "; ".join(problems))
        caption = await self._caption(data, chart_type, title)
        return ChartResponse(option=option, caption=caption)
