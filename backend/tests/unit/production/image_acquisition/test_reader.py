"""Durable ProductionVisualAssetPlan reader tests."""

import hashlib
import os
import stat
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from backend.src.production.domain.enums import ArtifactType
from backend.src.production.image_acquisition.exceptions import (
    ProductionVisualAssetPlanAmbiguousException,
    ProductionVisualAssetPlanChecksumException,
    ProductionVisualAssetPlanContractException,
    ProductionVisualAssetPlanEncodingException,
    ProductionVisualAssetPlanJsonException,
    ProductionVisualAssetPlanLinkException,
    ProductionVisualAssetPlanMissingFileException,
    ProductionVisualAssetPlanPathException,
    ProductionVisualAssetPlanSizeException,
    ProductionVisualAssetPlanTypeException,
    ProductionVisualAssetPlanVersionException,
)
from backend.src.production.image_acquisition.ports import (
    ProductionVisualAssetPlanArtifactCandidate,
)
from backend.src.production.infrastructure.durable_production_visual_asset_plan_reader import (
    DurableProductionVisualAssetPlanReader,
)
from backend.src.production.visual_asset_planning.serialization import (
    serialize_visual_asset_plan,
)
from backend.tests.unit.production.image_acquisition.conftest import (
    JOB_ID,
    NOW,
    VISUAL_PLAN_ARTIFACT_ID,
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


def candidate(content: bytes, *, path=None, **updates):
    values = {
        "artifact_id": VISUAL_PLAN_ARTIFACT_ID,
        "job_id": JOB_ID,
        "artifact_type": ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN,
        "relative_path": path
        or (
            f"production/{JOB_ID}/visual_asset_planning/attempt-1/"
            "visual-asset-plan.json"
        ),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "provider": "orion-simulated",
        "model_version": "visual-v1",
        "created_at": NOW,
        "metadata": {
            "schema_version": "1.0.0",
            "asset_count": 2,
            "authorization": "discard",
        },
    }
    values.update(updates)
    return ProductionVisualAssetPlanArtifactCandidate(**values)


def write(root: Path, item, content: bytes) -> Path:
    target = root.joinpath(*item.relative_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def reader(root, repository, *, maximum=200_000):
    return DurableProductionVisualAssetPlanReader(
        workspace_root=root,
        repository=repository,
        max_plan_bytes=maximum,
    )


@pytest.mark.asyncio
async def test_reads_explicit_current_job_and_safe_metadata(
    tmp_path,
    visual_asset_plan,
    image_command_context,
) -> None:
    content = serialize_visual_asset_plan(visual_asset_plan)
    item = candidate(content)
    target = write(tmp_path, item, content)
    before = target.read_bytes()
    repository = FakeRepository((item,))
    _, context = image_command_context
    result = await reader(tmp_path, repository).read_for_image_acquisition(
        context=context
    )
    assert result.visual_asset_plan == visual_asset_plan
    assert result.job_id == JOB_ID
    assert result.metadata == {"schema_version": "1.0.0", "asset_count": 2}
    assert repository.requested_job_id == JOB_ID
    assert target.read_bytes() == before


@pytest.mark.asyncio
async def test_fallback_is_deterministic(
    tmp_path,
    visual_asset_plan,
    image_command_context,
) -> None:
    content = serialize_visual_asset_plan(visual_asset_plan)
    first = candidate(content)
    latest_id = UUID("30000000-0000-4000-8000-000000000902")
    latest = candidate(
        content,
        artifact_id=latest_id,
        path=(
            f"production/{JOB_ID}/visual_asset_planning/attempt-2/"
            "visual-asset-plan.json"
        ),
        created_at=NOW + timedelta(seconds=1),
    )
    for item in (first, latest):
        write(tmp_path, item, content)
    _, context = image_command_context
    result = await reader(
        tmp_path,
        FakeRepository((first, latest)),
    ).read_for_image_acquisition(
        context=context.model_copy(update={"input_artifact_ids": ()})
    )
    assert result.artifact_id == latest_id


@pytest.mark.asyncio
async def test_multiple_explicit_inputs_are_ambiguous(
    tmp_path,
    visual_asset_plan,
    image_command_context,
) -> None:
    content = serialize_visual_asset_plan(visual_asset_plan)
    second_id = UUID("30000000-0000-4000-8000-000000000902")
    first = candidate(content)
    second = candidate(content, artifact_id=second_id)
    _, context = image_command_context
    with pytest.raises(ProductionVisualAssetPlanAmbiguousException):
        await reader(
            tmp_path,
            FakeRepository((first, second)),
        ).read_for_image_acquisition(
            context=context.model_copy(
                update={
                    "input_artifact_ids": (
                        VISUAL_PLAN_ARTIFACT_ID,
                        second_id,
                    )
                }
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            {"artifact_type": ArtifactType.PRODUCTION_SCENE_PLAN},
            ProductionVisualAssetPlanTypeException,
        ),
        (
            {"relative_path": "../visual-asset-plan.json"},
            ProductionVisualAssetPlanPathException,
        ),
        (
            {"relative_path": "C:/outside/visual-asset-plan.json"},
            ProductionVisualAssetPlanPathException,
        ),
        ({"size_bytes": 1}, ProductionVisualAssetPlanSizeException),
        ({"sha256": "0" * 64}, ProductionVisualAssetPlanChecksumException),
    ],
)
async def test_rejects_invalid_artifact_metadata(
    tmp_path,
    visual_asset_plan,
    image_command_context,
    mutation,
    error,
) -> None:
    content = serialize_visual_asset_plan(visual_asset_plan)
    item = candidate(content, **mutation)
    if item.relative_path.startswith("production/"):
        write(tmp_path, item, content)
    _, context = image_command_context
    with pytest.raises(error):
        await reader(
            tmp_path,
            FakeRepository((item,)),
        ).read_for_image_acquisition(context=context)


@pytest.mark.asyncio
async def test_rejects_missing_and_oversized_file(
    tmp_path,
    visual_asset_plan,
    image_command_context,
) -> None:
    content = serialize_visual_asset_plan(visual_asset_plan)
    item = candidate(content)
    _, context = image_command_context
    with pytest.raises(ProductionVisualAssetPlanMissingFileException):
        await reader(
            tmp_path,
            FakeRepository((item,)),
        ).read_for_image_acquisition(context=context)
    write(tmp_path, item, content)
    with pytest.raises(ProductionVisualAssetPlanSizeException):
        await reader(
            tmp_path,
            FakeRepository((item,)),
            maximum=10,
        ).read_for_image_acquisition(context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b"\xff", ProductionVisualAssetPlanEncodingException),
        (b"{", ProductionVisualAssetPlanJsonException),
        (b'{"value":NaN}', ProductionVisualAssetPlanJsonException),
        (b'{"value":1,"value":2}', ProductionVisualAssetPlanJsonException),
        (b"{}", ProductionVisualAssetPlanContractException),
    ],
)
async def test_rejects_invalid_content(
    tmp_path,
    image_command_context,
    content,
    error,
) -> None:
    item = candidate(content)
    write(tmp_path, item, content)
    _, context = image_command_context
    with pytest.raises(error):
        await reader(
            tmp_path,
            FakeRepository((item,)),
        ).read_for_image_acquisition(context=context)


@pytest.mark.asyncio
async def test_rejects_unsupported_version(
    tmp_path,
    visual_asset_plan,
    image_command_context,
) -> None:
    content = serialize_visual_asset_plan(
        visual_asset_plan.model_copy(update={"schema_version": "9.0.0"})
    )
    item = candidate(content)
    write(tmp_path, item, content)
    _, context = image_command_context
    with pytest.raises(ProductionVisualAssetPlanVersionException):
        await reader(
            tmp_path,
            FakeRepository((item,)),
        ).read_for_image_acquisition(context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize("link_part", ["file", "directory"])
async def test_rejects_link_components(
    tmp_path,
    visual_asset_plan,
    image_command_context,
    monkeypatch,
    link_part,
) -> None:
    content = serialize_visual_asset_plan(visual_asset_plan)
    item = candidate(content)
    target = write(tmp_path, item, content)
    simulated_link = target if link_part == "file" else target.parent
    original = os.lstat

    def fake_lstat(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(simulated_link):
            return SimpleNamespace(
                st_mode=stat.S_IFLNK,
                st_file_attributes=0,
            )
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fake_lstat)
    _, context = image_command_context
    with pytest.raises(ProductionVisualAssetPlanLinkException):
        await reader(
            tmp_path,
            FakeRepository((item,)),
        ).read_for_image_acquisition(context=context)
