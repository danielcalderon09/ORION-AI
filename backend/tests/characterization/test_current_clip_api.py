"""Minimal characterization of the currently operational video-to-clips API.

These tests intentionally call the controller functions directly and mock the
heavy processing path. They document current behavior without changing it.
"""

import importlib
import logging
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import BackgroundTasks, UploadFile


@pytest.fixture(scope="module")
def current_controllers(tmp_path_factory):
    """Import current controllers with all filesystem state isolated to pytest temp."""

    root = tmp_path_factory.mktemp("current_clip_api")
    patcher = pytest.MonkeyPatch()
    settings_module = importlib.import_module("backend.src.infrastructure.config.settings")
    patcher.setattr(settings_module.settings, "ORION_HOME", root / "home")
    patcher.setattr(settings_module.settings, "PROJECTS_DIR", root / "projects")
    settings_module.settings.ORION_HOME.mkdir(parents=True, exist_ok=True)
    settings_module.settings.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

    video_controller = importlib.import_module("backend.src.api.v1.video_controller")
    clip_controller = importlib.import_module("backend.src.api.v1.clip_controller")
    video_controller._progress_store.clear()

    yield video_controller, clip_controller, root

    video_controller._progress_store.clear()
    for handler in list(video_controller.logger.handlers):
        if isinstance(handler, logging.FileHandler):
            video_controller.logger.removeHandler(handler)
            handler.close()
    patcher.undo()


async def submit_without_processing(video_controller, monkeypatch, *, clip_count: int = 3):
    monkeypatch.setattr(video_controller, "get_orchestrator", lambda: object())
    background_tasks = BackgroundTasks()
    upload = UploadFile(filename="My source.mov", file=BytesIO(b"minimal-test-payload"))
    response = await video_controller.upload_video(
        background_tasks,
        upload,
        platform="tiktok",
        profile="balanced",
        clip_count=clip_count,
    )
    return response, background_tasks


@pytest.mark.asyncio
async def test_upload_response_project_id_and_folder_contract(
    current_controllers,
    monkeypatch,
) -> None:
    video_controller, _, _ = current_controllers

    response, background_tasks = await submit_without_processing(
        video_controller,
        monkeypatch,
        clip_count=4,
    )

    project_id = UUID(response["project_id"])
    source_file = video_controller.settings.PROJECTS_DIR / str(project_id) / "source" / "source_video.mp4"
    assert response == {
        "project_id": str(project_id),
        "name": "My source",
        "status": "processing",
        "platform": "tiktok",
        "profile": "balanced",
        "debug_mode": False,
        "clip_count": 4,
    }
    assert source_file.read_bytes() == b"minimal-test-payload"
    assert len(background_tasks.tasks) == 1


@pytest.mark.asyncio
async def test_progress_response_for_known_and_unknown_project(current_controllers) -> None:
    video_controller, _, _ = current_controllers
    project_id = "10000000-0000-4000-8000-000000000001"
    video_controller._progress_store[project_id] = {
        "percent": 60,
        "stage": "exporting",
        "status": "processing",
    }

    known = await video_controller.get_progress(project_id)
    unknown = await video_controller.get_progress("missing-project")

    assert known == {
        "project_id": project_id,
        "percent": 60,
        "stage": "exporting",
        "status": "processing",
    }
    assert unknown == {
        "project_id": "missing-project",
        "percent": 0,
        "stage": "unknown",
        "status": "not_found",
    }


@pytest.mark.asyncio
async def test_existing_clips_are_enumerated_by_mp4_name(current_controllers) -> None:
    _, clip_controller, _ = current_controllers
    project_id = "clip-list-project"
    exports = clip_controller.settings.PROJECTS_DIR / project_id / "exports"
    exports.mkdir(parents=True)
    (exports / "clip_001.mp4").write_bytes(b"one")
    (exports / "clip_002.mp4").write_bytes(b"two")
    (exports / "notes.txt").write_text("ignored", encoding="utf-8")

    response = await clip_controller.list_clips(project_id)

    clips = sorted(response["clips"], key=lambda item: item["filename"])
    assert [clip["clip_id"] for clip in clips] == ["clip_001", "clip_002"]
    assert [clip["filename"] for clip in clips] == ["clip_001.mp4", "clip_002.mp4"]
    assert all(Path(clip["path"]).is_absolute() for clip in clips)


@pytest.mark.asyncio
async def test_no_existing_clips_returns_empty_collection(current_controllers) -> None:
    _, clip_controller, _ = current_controllers

    response = await clip_controller.list_clips("project-without-clips")

    assert response == {"clips": []}


@pytest.mark.xfail(
    strict=True,
    reason="Known behavior: clip_count has no positive lower-bound validation.",
)
@pytest.mark.asyncio
async def test_clip_count_zero_should_be_rejected(current_controllers, monkeypatch) -> None:
    video_controller, _, _ = current_controllers

    response, _ = await submit_without_processing(video_controller, monkeypatch, clip_count=0)

    assert response["clip_count"] > 0


@pytest.mark.xfail(
    strict=True,
    reason='Known behavior: an extractor failure sets stage="failed" but status="completed".',
)
@pytest.mark.asyncio
async def test_extractor_error_should_report_failed_status(
    current_controllers,
    monkeypatch,
) -> None:
    video_controller, _, _ = current_controllers

    def fail_extraction(*args, **kwargs):
        raise RuntimeError("simulated extraction failure")

    monkeypatch.setattr(video_controller, "_extract_clips_fallback", fail_extraction)
    response, background_tasks = await submit_without_processing(video_controller, monkeypatch)
    await background_tasks()

    progress = await video_controller.get_progress(response["project_id"])
    assert progress["stage"] == "failed"
    assert progress["status"] == "failed"
