"""Durable ProductionScenePlan reader contract tests."""

import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from backend.src.production.domain.enums import ArtifactType
from backend.src.production.infrastructure.durable_production_scene_plan_reader import (
    DurableProductionScenePlanReader,
)
from backend.src.production.scene_planning.serialization import serialize_scene_plan
from backend.src.production.visual_asset_planning.exceptions import (
    ProductionScenePlanAmbiguousException,
    ProductionScenePlanChecksumException,
    ProductionScenePlanContractException,
    ProductionScenePlanEncodingException,
    ProductionScenePlanIntegrityException,
    ProductionScenePlanJsonException,
    ProductionScenePlanMissingFileException,
    ProductionScenePlanNotFoundException,
    ProductionScenePlanPathException,
    ProductionScenePlanSizeException,
    ProductionScenePlanSymlinkException,
    ProductionScenePlanTypeException,
    ProductionScenePlanVersionException,
)
from backend.src.production.visual_asset_planning.ports import (
    ProductionScenePlanArtifactCandidate,
)
from backend.tests.unit.production.visual_asset_planning.conftest import (
    JOB_ID,
    NOW,
    SCENE_PLAN_ARTIFACT_ID,
)


class FakeRepository:
    def __init__(self, candidates=(), input_types=None) -> None:
        self.candidates = tuple(candidates)
        self.input_types = input_types or {}
        self.requested_job_id = None

    def list_candidates(self, *, job_id):
        self.requested_job_id = job_id
        return self.candidates

    def list_input_artifact_types(self, *, job_id, artifact_ids):
        self.requested_job_id = job_id
        return {
            artifact_id: self.input_types[artifact_id]
            for artifact_id in artifact_ids
            if artifact_id in self.input_types
        }


def candidate(content: bytes, *, path: str | None = None, **updates):
    values = {
        "artifact_id": SCENE_PLAN_ARTIFACT_ID,
        "job_id": JOB_ID,
        "artifact_type": ArtifactType.PRODUCTION_SCENE_PLAN,
        "relative_path": path or f"production/{JOB_ID}/scene_planning/attempt-1/scene-plan.json",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "provider": "orion-simulated",
        "model_version": "scene-planning-simulator-v1",
        "created_at": NOW,
        "metadata": {
            "schema_version": "1.0.0",
            "shot_count": 4,
            "private_unrecognized": "discarded",
        },
    }
    values.update(updates)
    return ProductionScenePlanArtifactCandidate(**values)


def write_candidate(root, item, content: bytes) -> None:
    target = root.joinpath(*item.relative_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def reader(root, repository, *, limit=100_000):
    return DurableProductionScenePlanReader(
        workspace_root=root,
        repository=repository,
        max_scene_plan_bytes=limit,
    )


@pytest.mark.asyncio
async def test_reader_returns_immutable_verified_current_job_and_safe_metadata(
    tmp_path,
    production_scene_plan,
    visual_asset_command_context,
) -> None:
    content = serialize_scene_plan(production_scene_plan)
    item = candidate(content)
    write_candidate(tmp_path, item, content)
    repository = FakeRepository((item,))
    _, context = visual_asset_command_context
    before = context.model_dump_json()
    result = await reader(tmp_path, repository).read_for_visual_asset_planning(context=context)
    assert result.scene_plan == production_scene_plan
    assert result.artifact_id == SCENE_PLAN_ARTIFACT_ID
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert result.metadata == {"schema_version": "1.0.0", "shot_count": 4}
    assert repository.requested_job_id == JOB_ID
    assert context.model_dump_json() == before


@pytest.mark.asyncio
async def test_reader_prefers_explicit_and_falls_back_deterministically(
    tmp_path,
    production_scene_plan,
    visual_asset_command_context,
) -> None:
    content = serialize_scene_plan(production_scene_plan)
    latest_id = UUID("30000000-0000-4000-8000-000000000802")
    preferred = candidate(content)
    latest = candidate(
        content,
        artifact_id=latest_id,
        path=f"production/{JOB_ID}/scene_planning/attempt-2/scene-plan.json",
        created_at=NOW + timedelta(seconds=1),
    )
    for item in (preferred, latest):
        write_candidate(tmp_path, item, content)
    repository = FakeRepository((latest, preferred))
    _, context = visual_asset_command_context
    selected = await reader(tmp_path, repository).read_for_visual_asset_planning(context=context)
    assert selected.artifact_id == SCENE_PLAN_ARTIFACT_ID
    fallback = await reader(tmp_path, repository).read_for_visual_asset_planning(
        context=context.model_copy(update={"input_artifact_ids": ()})
    )
    assert fallback.artifact_id == latest_id


@pytest.mark.asyncio
async def test_reader_rejects_ambiguous_and_wrong_input_type(
    tmp_path,
    production_scene_plan,
    visual_asset_command_context,
) -> None:
    content = serialize_scene_plan(production_scene_plan)
    second_id = UUID("30000000-0000-4000-8000-000000000802")
    first = candidate(content)
    second = candidate(
        content,
        artifact_id=second_id,
        path=f"production/{JOB_ID}/scene_planning/attempt-2/scene-plan.json",
    )
    _, context = visual_asset_command_context
    ambiguous_context = context.model_copy(
        update={"input_artifact_ids": (first.artifact_id, second.artifact_id)}
    )
    with pytest.raises(ProductionScenePlanAmbiguousException):
        await reader(tmp_path, FakeRepository((first, second))).read_for_visual_asset_planning(
            context=ambiguous_context
        )

    wrong_id = UUID("90000000-0000-4000-8000-000000000801")
    wrong_context = context.model_copy(update={"input_artifact_ids": (wrong_id,)})
    with pytest.raises(ProductionScenePlanTypeException):
        await reader(
            tmp_path,
            FakeRepository(
                (),
                input_types={wrong_id: ArtifactType.PRODUCTION_SCRIPT},
            ),
        ).read_for_visual_asset_planning(context=wrong_context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("updates", "error_type"),
    [
        ({"size_bytes": 1}, ProductionScenePlanSizeException),
        ({"sha256": "0" * 64}, ProductionScenePlanChecksumException),
        (
            {"relative_path": "../scene-plan.json"},
            ProductionScenePlanPathException,
        ),
        (
            {"relative_path": "C:\\scene-plan.json"},
            ProductionScenePlanPathException,
        ),
        (
            {"job_id": UUID("10000000-0000-4000-8000-000000000899")},
            ProductionScenePlanIntegrityException,
        ),
        (
            {"artifact_type": ArtifactType.PRODUCTION_SCRIPT},
            ProductionScenePlanTypeException,
        ),
    ],
)
async def test_reader_rejects_integrity_path_job_and_type(
    tmp_path,
    production_scene_plan,
    visual_asset_command_context,
    updates,
    error_type,
) -> None:
    content = serialize_scene_plan(production_scene_plan)
    item = candidate(content, **updates)
    if (
        item.relative_path.startswith("production/")
        and item.job_id == JOB_ID
        and item.artifact_type is ArtifactType.PRODUCTION_SCENE_PLAN
    ):
        write_candidate(tmp_path, item, content)
    _, context = visual_asset_command_context
    with pytest.raises(error_type):
        await reader(
            tmp_path,
            FakeRepository((item,)),
        ).read_for_visual_asset_planning(context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "error_type"),
    [
        (b"\xff", ProductionScenePlanEncodingException),
        (b"{", ProductionScenePlanJsonException),
        (
            b'{"schema_version":"1.0.0","schema_version":"1.0.0"}',
            ProductionScenePlanJsonException,
        ),
        (b'{"target_duration_seconds":NaN}', ProductionScenePlanJsonException),
        (b"{}", ProductionScenePlanContractException),
    ],
)
async def test_reader_rejects_encoding_strict_json_duplicates_and_schema(
    tmp_path,
    visual_asset_command_context,
    content,
    error_type,
) -> None:
    item = candidate(content)
    write_candidate(tmp_path, item, content)
    _, context = visual_asset_command_context
    with pytest.raises(error_type):
        await reader(
            tmp_path,
            FakeRepository((item,)),
        ).read_for_visual_asset_planning(context=context)


@pytest.mark.asyncio
async def test_reader_reports_absent_missing_oversized_and_version(
    tmp_path,
    production_scene_plan,
    visual_asset_command_context,
) -> None:
    _, context = visual_asset_command_context
    with pytest.raises(ProductionScenePlanNotFoundException):
        await reader(
            tmp_path,
            FakeRepository(),
        ).read_for_visual_asset_planning(
            context=context.model_copy(update={"input_artifact_ids": ()})
        )
    content = serialize_scene_plan(production_scene_plan)
    item = candidate(content)
    with pytest.raises(ProductionScenePlanMissingFileException):
        await reader(
            tmp_path,
            FakeRepository((item,)),
        ).read_for_visual_asset_planning(context=context)
    write_candidate(tmp_path, item, content)
    with pytest.raises(ProductionScenePlanSizeException):
        await reader(
            tmp_path,
            FakeRepository((item,)),
            limit=10,
        ).read_for_visual_asset_planning(context=context)

    unsupported = production_scene_plan.model_copy(update={"schema_version": "2.0.0"})
    unsupported_content = serialize_scene_plan(unsupported)
    unsupported_item = candidate(unsupported_content)
    write_candidate(tmp_path, unsupported_item, unsupported_content)
    with pytest.raises(ProductionScenePlanVersionException):
        await reader(
            tmp_path,
            FakeRepository((unsupported_item,)),
        ).read_for_visual_asset_planning(context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize("symlink_part", ["file", "directory"])
async def test_reader_rejects_file_and_directory_symlinks(
    monkeypatch,
    tmp_path,
    production_scene_plan,
    visual_asset_command_context,
    symlink_part,
) -> None:
    content = serialize_scene_plan(production_scene_plan)
    item = candidate(content)
    target = tmp_path.joinpath(*item.relative_path.split("/"))
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    simulated_symlink = target if symlink_part == "file" else target.parent
    original_is_symlink = Path.is_symlink

    def fake_is_symlink(path):
        return path == simulated_symlink or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    _, context = visual_asset_command_context
    with pytest.raises(ProductionScenePlanSymlinkException):
        await reader(
            tmp_path,
            FakeRepository((item,)),
        ).read_for_visual_asset_planning(context=context)
