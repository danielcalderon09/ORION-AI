"""Clip controller for accessing exported clips."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.src.infrastructure.config.settings import settings

router = APIRouter()


@router.get("/{project_id}")
async def list_clips(project_id: str):
    """List all clips for a project."""
    exports_dir = settings.PROJECTS_DIR / project_id / "exports"
    clips = []

    if exports_dir.exists():
        for f in exports_dir.iterdir():
            if f.suffix == ".mp4":
                clips.append({
                    "clip_id": f.stem,
                    "filename": f.name,
                    "path": str(f),
                })

    return {"clips": clips}


@router.get("/{project_id}/download/{clip_id}")
async def download_clip(project_id: str, clip_id: str):
    """Stream/download a specific clip."""
    clip_path = settings.PROJECTS_DIR / project_id / "exports" / f"{clip_id}.mp4"
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")
    return FileResponse(
        clip_path,
        media_type="video/mp4",
        filename=clip_path.name,
        content_disposition_type="inline",
    )
