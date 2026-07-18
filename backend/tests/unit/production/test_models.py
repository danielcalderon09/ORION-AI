"""Unit tests for the prompt-to-video domain contracts."""

import ast
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.domain import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    AssetType,
    EditPackage,
    MotionType,
    ProductionJob,
    ProductionJobStatus,
    ProductionPlan,
    ProductionStage,
    ScenePlan,
    TransitionType,
)

FIXTURE_PATH = (
    Path(__file__).parents[2] / "fixtures" / "production" / "edit_package_example.json"
)


def make_scene(*, order: int, duration: float = 5.0) -> ScenePlan:
    return ScenePlan(
        scene_id=UUID(f"20000000-0000-4000-8000-{order + 1:012d}"),
        order=order,
        duration_seconds=duration,
        narration_text=f"Narration {order}",
        visual_description=f"Visual {order}",
        asset_query=f"Asset {order}",
        asset_type=AssetType.IMAGE,
        motion=MotionType.STATIC,
        transition=TransitionType.CUT,
    )


def make_plan(*, duration: float = 10.0, scenes: list[ScenePlan] | None = None) -> ProductionPlan:
    return ProductionPlan(
        version="1.0.0",
        title="Validated plan",
        original_prompt="Create a concise vertical story",
        target_platform="short-form",
        language="es-CO",
        duration_seconds=duration,
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        fps=30.0,
        style="documentary",
        audience="general",
        narration_style="warm",
        music_style="ambient",
        generate_clips_after_render=True,
        scenes=scenes or [make_scene(order=0), make_scene(order=1)],
    )


def test_valid_production_plan() -> None:
    plan = make_plan()

    assert plan.duration_seconds == 10.0
    assert plan.aspect_ratio == "9:16"
    assert plan.scenes[0].asset_type is AssetType.IMAGE


@pytest.mark.parametrize("duration", [0, -1])
def test_production_plan_rejects_non_positive_duration(duration: float) -> None:
    with pytest.raises(ValidationError, match="duration_seconds"):
        make_plan(duration=duration)


def test_production_plan_rejects_duplicate_scene_order() -> None:
    scenes = [make_scene(order=0), make_scene(order=0)]

    with pytest.raises(ValidationError, match="scene order values must be unique"):
        make_plan(scenes=scenes)


def test_edit_package_validates_from_json_fixture() -> None:
    package = EditPackage.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert package.width == 1080
    assert package.height == 1920
    assert package.fps == 30.0
    assert len(package.scenes) == 4
    assert package.output_relative_path.endswith(".mp4")


@pytest.mark.parametrize(
    "unsafe_path",
    [
        r"C:\\Users\\someone\\private.mp4",
        "/tmp/private.mp4",
        "assets/../private.mp4",
    ],
    ids=["windows-absolute", "posix-absolute", "path-traversal"],
)
def test_artifact_rejects_unsafe_paths(unsafe_path: str) -> None:
    with pytest.raises(ValidationError):
        Artifact(
            job_id=UUID("10000000-0000-4000-8000-000000000001"),
            artifact_type=ArtifactType.LONG_FORM_RENDER,
            relative_path=unsafe_path,
            mime_type="video/mp4",
        )


@pytest.mark.parametrize("fps", [0, -30])
def test_edit_package_rejects_non_positive_fps(fps: float) -> None:
    payload = EditPackage.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8")).model_dump()
    payload["fps"] = fps

    with pytest.raises(ValidationError, match="fps"):
        EditPackage.model_validate(payload)


def test_serialization_round_trip_is_stable() -> None:
    original = EditPackage.model_validate_json(FIXTURE_PATH.read_text(encoding="utf-8"))
    serialized = original.model_dump_json()
    restored = EditPackage.model_validate_json(serialized)

    assert restored == original
    assert restored.model_dump(mode="json") == original.model_dump(mode="json")


def test_ids_and_enums_are_typed() -> None:
    job = ProductionJob(
        job_id="10000000-0000-4000-8000-000000000001",
        prompt="Create a video",
        status="queued",
        current_stage="planning",
    )

    assert isinstance(job.job_id, UUID)
    assert job.status is ProductionJobStatus.QUEUED
    assert job.current_stage is ProductionStage.PLANNING

    artifact = Artifact(
        job_id=job.job_id,
        artifact_type="source_image",
        relative_path="assets/image.png",
        mime_type="image/png",
        status="ready",
    )
    assert artifact.artifact_type is ArtifactType.SOURCE_IMAGE
    assert artifact.status is ArtifactStatus.READY


def test_production_plan_rejects_incoherent_scene_duration() -> None:
    with pytest.raises(ValidationError, match="total scene duration"):
        make_plan(duration=11.0)


def test_domain_has_no_heavy_runtime_imports() -> None:
    domain_dir = Path(__file__).parents[3] / "src" / "production" / "domain"
    forbidden = {"fastapi", "cv2", "ffmpeg", "davinci", "DaVinciResolveScript"}

    imported_roots: set[str] = set()
    for source_path in domain_dir.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])

    assert imported_roots.isdisjoint(forbidden)
