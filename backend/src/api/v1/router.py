"""API V1 routers."""

from fastapi import APIRouter

from backend.src.api.v1.video_controller import router as video_router
from backend.src.api.v1.clip_controller import router as clip_router
from backend.src.api.dashboard.dashboard_controller import router as dashboard_router

api_router = APIRouter()
api_router.include_router(video_router, prefix="/videos", tags=["videos"])
api_router.include_router(clip_router, prefix="/clips", tags=["clips"])
api_router.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
