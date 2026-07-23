"""Durable ProductionScript reader contract tests."""

import hashlib
from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.infrastructure.durable_production_script_reader import (
    DurableProductionScriptReader,
)
from backend.src.production.scene_planning.exceptions import (
    ProductionScriptChecksumException,
    ProductionScriptContractException,
    ProductionScriptEncodingException,
    ProductionScriptIntegrityException,
    ProductionScriptJsonException,
    ProductionScriptMissingFileException,
    ProductionScriptNotFoundException,
    ProductionScriptPathException,
    ProductionScriptSizeException,
    ProductionScriptVersionException,
)
from backend.src.production.scene_planning.ports import ProductionScriptArtifactCandidate
from backend.src.production.scripting.serialization import serialize_production_script
from backend.tests.unit.production.scene_planning.conftest import (
    JOB_ID,
    NOW,
    SCRIPT_ARTIFACT_ID,
)


class FakeRepository:
    def __init__(self, candidates=()) -> None:
        self.candidates = tuple(candidates)
        self.requested_job_id = None

    def list_candidates(self, *, job_id):
        self.requested_job_id = job_id
        return self.candidates


def candidate(content: bytes, *, path: str | None = None, **updates):
    values = {
        "artifact_id": SCRIPT_ARTIFACT_ID,
        "job_id": JOB_ID,
        "relative_path": path
        or f"production/{JOB_ID}/scripting/attempt-1/production-script.json",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "provider": "orion-simulated",
        "model_version": "scripting-simulator-v1",
        "created_at": NOW,
    }
    values.update(updates)
    return ProductionScriptArtifactCandidate(**values)


def write_candidate(root, item, content: bytes) -> None:
    target = root.joinpath(*item.relative_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


@pytest.mark.asyncio
async def test_reader_returns_immutable_verified_script_for_current_job(
    tmp_path, production_script, scene_planning_command_context
) -> None:
    content = serialize_production_script(production_script)
    item = candidate(content)
    write_candidate(tmp_path, item, content)
    repository = FakeRepository((item,))
    reader = DurableProductionScriptReader(
        workspace_root=tmp_path,
        repository=repository,
        max_script_bytes=100_000,
    )
    _, context = scene_planning_command_context
    snapshot = context.model_dump_json()
    result = await reader.read_for_scene_planning(context=context)
    assert result.script == production_script
    assert result.artifact_id == SCRIPT_ARTIFACT_ID
    assert repository.requested_job_id == JOB_ID
    assert context.model_dump_json() == snapshot
    with pytest.raises(ValidationError):
        result.size_bytes = 0


@pytest.mark.asyncio
async def test_reader_prefers_input_artifact_then_latest_attempt(
    tmp_path, production_script, scene_planning_command_context
) -> None:
    content = serialize_production_script(production_script)
    latest_id = UUID("30000000-0000-4000-8000-000000000702")
    preferred = candidate(content)
    latest = candidate(
        content,
        artifact_id=latest_id,
        path=f"production/{JOB_ID}/scripting/attempt-2/production-script.json",
        created_at=NOW + timedelta(seconds=1),
    )
    for item in (preferred, latest):
        write_candidate(tmp_path, item, content)
    reader = DurableProductionScriptReader(
        workspace_root=tmp_path,
        repository=FakeRepository((latest, preferred)),
        max_script_bytes=100_000,
    )
    _, context = scene_planning_command_context
    assert (
        await reader.read_for_scene_planning(context=context)
    ).artifact_id == SCRIPT_ARTIFACT_ID
    retry_context = context.model_copy(update={"input_artifact_ids": ()})
    assert (
        await reader.read_for_scene_planning(context=retry_context)
    ).artifact_id == latest_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "error_type"),
    [
        ({"size_bytes": 1}, ProductionScriptSizeException),
        ({"sha256": "0" * 64}, ProductionScriptChecksumException),
        ({"path": "../production-script.json"}, ProductionScriptPathException),
        ({"path": "C:\\production-script.json"}, ProductionScriptPathException),
        (
            {"job_id": UUID("10000000-0000-4000-8000-000000000799")},
            ProductionScriptIntegrityException,
        ),
    ],
)
async def test_reader_rejects_bad_integrity_and_paths(
    tmp_path,
    production_script,
    scene_planning_command_context,
    mutation,
    error_type,
) -> None:
    content = serialize_production_script(production_script)
    path = mutation.pop("path", None)
    item = candidate(content, path=path, **mutation)
    if path is None and item.job_id == JOB_ID:
        write_candidate(tmp_path, item, content)
    reader = DurableProductionScriptReader(
        workspace_root=tmp_path,
        repository=FakeRepository((item,)),
        max_script_bytes=100_000,
    )
    _, context = scene_planning_command_context
    with pytest.raises(error_type):
        await reader.read_for_scene_planning(context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "error_type"),
    [
        (b"\xff", ProductionScriptEncodingException),
        (b"{", ProductionScriptJsonException),
        (b'{"schema_version":"1.0.0","schema_version":"1.0.0"}', ProductionScriptJsonException),
        (b"{}", ProductionScriptContractException),
    ],
)
async def test_reader_rejects_invalid_utf8_json_duplicates_and_contract(
    tmp_path, scene_planning_command_context, content, error_type
) -> None:
    item = candidate(content)
    write_candidate(tmp_path, item, content)
    reader = DurableProductionScriptReader(
        workspace_root=tmp_path,
        repository=FakeRepository((item,)),
        max_script_bytes=100_000,
    )
    _, context = scene_planning_command_context
    with pytest.raises(error_type):
        await reader.read_for_scene_planning(context=context)


@pytest.mark.asyncio
async def test_reader_reports_absent_missing_oversized_and_unsupported(
    tmp_path, production_script, scene_planning_command_context
) -> None:
    _, context = scene_planning_command_context
    empty = DurableProductionScriptReader(
        workspace_root=tmp_path,
        repository=FakeRepository(),
        max_script_bytes=100_000,
    )
    with pytest.raises(ProductionScriptNotFoundException):
        await empty.read_for_scene_planning(context=context)
    content = serialize_production_script(production_script)
    item = candidate(content)
    missing = DurableProductionScriptReader(
        workspace_root=tmp_path,
        repository=FakeRepository((item,)),
        max_script_bytes=100_000,
    )
    with pytest.raises(ProductionScriptMissingFileException):
        await missing.read_for_scene_planning(context=context)
    write_candidate(tmp_path, item, content)
    oversized = DurableProductionScriptReader(
        workspace_root=tmp_path,
        repository=FakeRepository((item,)),
        max_script_bytes=10,
    )
    with pytest.raises(ProductionScriptSizeException):
        await oversized.read_for_scene_planning(context=context)
    unsupported_script = production_script.model_copy(update={"schema_version": "2.0.0"})
    unsupported_content = serialize_production_script(unsupported_script)
    unsupported_item = candidate(unsupported_content)
    write_candidate(tmp_path, unsupported_item, unsupported_content)
    unsupported = DurableProductionScriptReader(
        workspace_root=tmp_path,
        repository=FakeRepository((unsupported_item,)),
        max_script_bytes=100_000,
    )
    with pytest.raises(ProductionScriptVersionException):
        await unsupported.read_for_scene_planning(context=context)
