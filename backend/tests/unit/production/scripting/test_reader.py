"""Contract tests for metadata-driven durable ProductionPlan reads."""

import hashlib
from datetime import timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.infrastructure.durable_production_plan_reader import (
    DurableProductionPlanReader,
)
from backend.src.production.planning.serialization import serialize_production_plan
from backend.src.production.scripting.exceptions import (
    ProductionPlanChecksumError,
    ProductionPlanContractError,
    ProductionPlanEncodingError,
    ProductionPlanIntegrityError,
    ProductionPlanJsonError,
    ProductionPlanMissingFileError,
    ProductionPlanNotFoundError,
    ProductionPlanPathError,
    ProductionPlanSizeError,
    ProductionPlanVersionError,
)
from backend.src.production.scripting.ports import ProductionPlanArtifactCandidate
from backend.tests.unit.production.scripting.conftest import JOB_ID, NOW, PLAN_ARTIFACT_ID


class FakeRepository:
    def __init__(self, candidates=()) -> None:
        self.candidates = tuple(candidates)
        self.requested_job_id = None

    def list_candidates(self, *, job_id):
        self.requested_job_id = job_id
        return self.candidates


def candidate(content: bytes, *, path: str | None = None, **updates):
    values = {
        "artifact_id": PLAN_ARTIFACT_ID,
        "job_id": JOB_ID,
        "relative_path": path
        or f"production/{JOB_ID}/planning/attempt-1/production-plan.json",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "provider": "orion-simulated",
        "model_version": "planning-simulator-v1",
        "metadata": {"schema_version": "1.0.0"},
        "created_at": NOW,
    }
    values.update(updates)
    return ProductionPlanArtifactCandidate(**values)


def write_candidate(root, item, content: bytes) -> None:
    target = root.joinpath(*item.relative_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


@pytest.mark.asyncio
async def test_reader_finds_job_plan_and_returns_immutable_contract(
    tmp_path, production_plan, scripting_command_context
) -> None:
    content = serialize_production_plan(production_plan)
    item = candidate(content)
    write_candidate(tmp_path, item, content)
    repository = FakeRepository((item,))
    reader = DurableProductionPlanReader(
        workspace_root=tmp_path, repository=repository, max_plan_bytes=100_000
    )
    _, context = scripting_command_context
    snapshot = context.model_dump_json()
    read = await reader.read_for_scripting(context=context)
    assert read.plan == production_plan
    assert read.artifact_id == PLAN_ARTIFACT_ID
    assert repository.requested_job_id == JOB_ID
    assert context.model_dump_json() == snapshot
    with pytest.raises(ValidationError):
        read.size_bytes = 0


@pytest.mark.asyncio
async def test_reader_selects_preferred_then_latest_attempt(
    tmp_path, production_plan, scripting_command_context
) -> None:
    content = serialize_production_plan(production_plan)
    older_id = UUID("30000000-0000-4000-8000-000000000602")
    preferred = candidate(content)
    latest = candidate(
        content,
        artifact_id=older_id,
        path=f"production/{JOB_ID}/planning/attempt-2/production-plan.json",
        created_at=NOW + timedelta(seconds=1),
    )
    for item in (preferred, latest):
        write_candidate(tmp_path, item, content)
    _, context = scripting_command_context
    reader = DurableProductionPlanReader(
        workspace_root=tmp_path,
        repository=FakeRepository((latest, preferred)),
        max_plan_bytes=100_000,
    )
    assert (await reader.read_for_scripting(context=context)).artifact_id == PLAN_ARTIFACT_ID
    retry_context = context.model_copy(update={"input_artifact_ids": ()})
    assert (await reader.read_for_scripting(context=retry_context)).artifact_id == older_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "error_type"),
    [
        ({"size_bytes": 1}, ProductionPlanSizeError),
        ({"sha256": "0" * 64}, ProductionPlanChecksumError),
        ({"path": "../production-plan.json"}, ProductionPlanPathError),
        ({"path": "C:\\production-plan.json"}, ProductionPlanPathError),
        (
            {"job_id": UUID("10000000-0000-4000-8000-000000000699")},
            ProductionPlanIntegrityError,
        ),
    ],
)
async def test_reader_rejects_bad_metadata_or_path(
    tmp_path, production_plan, scripting_command_context, mutation, error_type
) -> None:
    content = serialize_production_plan(production_plan)
    path = mutation.pop("path", None)
    item = candidate(content, path=path, **mutation)
    if path is None and item.job_id == JOB_ID:
        write_candidate(tmp_path, item, content)
    _, context = scripting_command_context
    reader = DurableProductionPlanReader(
        workspace_root=tmp_path, repository=FakeRepository((item,)), max_plan_bytes=100_000
    )
    with pytest.raises(error_type):
        await reader.read_for_scripting(context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "error_type"),
    [
        (b"\xff", ProductionPlanEncodingError),
        (b"{", ProductionPlanJsonError),
        (b"{}", ProductionPlanContractError),
    ],
)
async def test_reader_rejects_invalid_file_content(
    tmp_path, scripting_command_context, content, error_type
) -> None:
    item = candidate(content)
    write_candidate(tmp_path, item, content)
    _, context = scripting_command_context
    reader = DurableProductionPlanReader(
        workspace_root=tmp_path, repository=FakeRepository((item,)), max_plan_bytes=100_000
    )
    with pytest.raises(error_type):
        await reader.read_for_scripting(context=context)


@pytest.mark.asyncio
async def test_reader_reports_absent_missing_oversized_and_unsupported(
    tmp_path, production_plan, scripting_command_context
) -> None:
    _, context = scripting_command_context
    empty = DurableProductionPlanReader(
        workspace_root=tmp_path, repository=FakeRepository(), max_plan_bytes=100_000
    )
    with pytest.raises(ProductionPlanNotFoundError):
        await empty.read_for_scripting(context=context)
    content = serialize_production_plan(production_plan)
    item = candidate(content)
    missing = DurableProductionPlanReader(
        workspace_root=tmp_path, repository=FakeRepository((item,)), max_plan_bytes=100_000
    )
    with pytest.raises(ProductionPlanMissingFileError):
        await missing.read_for_scripting(context=context)
    write_candidate(tmp_path, item, content)
    oversized = DurableProductionPlanReader(
        workspace_root=tmp_path, repository=FakeRepository((item,)), max_plan_bytes=10
    )
    with pytest.raises(ProductionPlanSizeError):
        await oversized.read_for_scripting(context=context)
    unsupported_plan = production_plan.model_copy(update={"schema_version": "2.0.0"})
    unsupported_content = serialize_production_plan(unsupported_plan)
    unsupported_item = candidate(unsupported_content)
    write_candidate(tmp_path, unsupported_item, unsupported_content)
    unsupported = DurableProductionPlanReader(
        workspace_root=tmp_path,
        repository=FakeRepository((unsupported_item,)),
        max_plan_bytes=100_000,
    )
    with pytest.raises(ProductionPlanVersionError):
        await unsupported.read_for_scripting(context=context)
