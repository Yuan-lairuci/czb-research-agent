"""模块四 API：论文检索追踪与智能推荐（/api/recommend/*、/api/track/*）。

独立可测：create_recommend_router(data_dir=..., s2=..., arxiv=...) 可注入
数据目录与两个 API 客户端（零网络单测）；默认 router 使用 backend/data/。
main.py 注册（由调度员集成）：

    from app.api.recommend import router as recommend_router
    app.include_router(recommend_router)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.models.recommend_models import (
    ByPaperRequest,
    ByPaperResponse,
    FeedbackRequest,
    FeedbackResponse,
    FetchRequest,
    FetchResponse,
    SearchRequest,
    SearchResponse,
    TrackAddRequest,
    TrackItem,
    TrackListResponse,
    TrackRunResponse,
)
from app.services.arxiv_client import ArxivClient, ArxivError
from app.services.recommend import (
    FeedbackStore,
    fetch_and_ingest,
    recommend_by_paper,
    search_merged,
)
from app.services.s2_client import S2Client, S2Error
from app.services.track import TrackService, TrackStore

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def create_recommend_router(
    data_dir: Path | str | None = None,
    *,
    s2: S2Client | None = None,
    arxiv: ArxivClient | None = None,
) -> APIRouter:
    """构建检索追踪推荐路由；data_dir 缺省为 backend/data/。"""
    data_root = Path(data_dir) if data_dir is not None else _DEFAULT_DATA_DIR
    (data_root / "recommend").mkdir(parents=True, exist_ok=True)

    s2_client = s2 if s2 is not None else S2Client()
    arxiv_client = arxiv if arxiv is not None else ArxivClient()
    feedback_store = FeedbackStore(data_root / "recommend" / "feedback.db")
    track_service = TrackService(
        TrackStore(data_root / "recommend" / "track.db"), arxiv=arxiv_client
    )

    router = APIRouter()

    @router.post("/api/recommend/search", response_model=SearchResponse)
    async def recommend_search(req: SearchRequest) -> SearchResponse:
        """双源（S2+arXiv）合并检索；S2 不可达降级为 arxiv_only。"""
        try:
            return search_merged(req.query, req.limit, s2=s2_client, arxiv=arxiv_client)
        except RuntimeError as exc:  # 双源均不可达
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/api/recommend/fetch", response_model=FetchResponse)
    async def recommend_fetch(req: FetchRequest) -> FetchResponse:
        """下载 arXiv PDF 并走模块一入库解析。"""
        try:
            return fetch_and_ingest(req.arxiv_id, arxiv=arxiv_client, data_dir=data_root)
        except ArxivError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/recommend/by-paper", response_model=ByPaperResponse)
    async def recommend_for_paper(req: ByPaperRequest) -> ByPaperResponse:
        """基于论文 references/citations 的推荐（含金量过滤 + 排序分）。"""
        try:
            return recommend_by_paper(
                req.paper_id, req.top_k, s2=s2_client, store=feedback_store
            )
        except S2Error as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/api/recommend/feedback", response_model=FeedbackResponse)
    async def recommend_feedback(req: FeedbackRequest) -> FeedbackResponse:
        """点赞/点踩反馈，影响后续排序。"""
        if not req.s2_id and not req.arxiv_id:
            raise HTTPException(status_code=400, detail="s2_id or arxiv_id required")
        return feedback_store.record(req.s2_id, req.arxiv_id, req.like)

    @router.post("/api/track/add", response_model=TrackItem)
    async def track_add(req: TrackAddRequest) -> TrackItem:
        """登记追踪论文。"""
        try:
            return track_service.add(req.arxiv_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ArxivError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/api/track/list", response_model=TrackListResponse)
    async def track_list() -> TrackListResponse:
        """追踪列表。"""
        return TrackListResponse(items=track_service.list_items())

    @router.post("/api/track/run", response_model=TrackRunResponse)
    async def track_run() -> TrackRunResponse:
        """版本巡检：检查 arXiv 新版本并列出更新。"""
        return track_service.run()

    return router


router = create_recommend_router()
