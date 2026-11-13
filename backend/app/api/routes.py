"""API 路由。"""
from __future__ import annotations

from fastapi import APIRouter

from app.models.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """健康检查：返回服务状态与版本。"""
    return HealthResponse(status="ok", version="0.1.0")
