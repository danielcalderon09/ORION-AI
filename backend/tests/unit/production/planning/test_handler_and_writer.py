"""PlanningHandler and artifact writer contract tests."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.planning.artifact_writer import (
    InMemoryPlanningArtifactWriter,
    LocalPlanningArtifactWriter,
)
from backend.src.production.planning.exceptions import (
    PlanningProviderAuthenticationError,
    PlanningProviderRateLimitError,
    PlanningProviderTimeoutError,
)
from backend.src.production.planning.providers import SimulatedPlanningProvider
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.handlers import PlanningHandler

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def command_and_context(configuration: dict | None = None):
    command = StageCommand(
        command_id=UUID("20000000-0000-4000-8000-000000000502"),
        job_id=UUID("10000000-0000-4000-8000-000000000502"),
        stage=ProductionStage.PLANNING,
        attempt_number=1,
        idempotency_key="planning:test",
        configuration_snapshot={"configuration": configuration or {}},
        created_at=NOW,
    )
    context = StageContext(
        job_id=command.job_id,
        command_id=command.command_id,
        stage=command.stage,
        attempt_number=1,
        job_prompt="Create a safe educational video",
        job_configuration=command.configuration_snapshot,
        workspace_relative_path=f"production/{command.job_id}/planning/attempt-1",
        correlation_id=command.job_id,
    )
    return command, context


def handler(provider, writer) -> PlanningHandler:
    return PlanningHandler(
        provider=provider,
        artifact_writer=writer,
        clock=lambda: NOW,
        uuid_factory=lambda: UUID("40000000-0000-4000-8000-000000000502"),
    )


@pytest.mark.asyncio
async def test_handler_writes_exact_canonical_artifact() -> None:
    class CountingProvider(SimulatedPlanningProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def generate_plan(self, request):
            self.calls += 1
            return await super().generate_plan(request)

    writer = InMemoryPlanningArtifactWriter()
    provider = CountingProvider()
    command, context = command_and_context(
        {"language": "en", "target_duration_seconds": 20, "scene_count_hint": 2}
    )
    output = await handler(provider, writer).execute(command, context)
    artifact = output.artifacts[0]
    content = writer.contents[artifact.relative_path]
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert artifact.artifact_type is ArtifactType.PRODUCTION_PLAN
    assert artifact.relative_path.endswith("/planning/attempt-1/production-plan.json")
    assert "\\" not in artifact.relative_path
    assert artifact.size_bytes == len(content)
    assert artifact.sha256 == hashlib.sha256(content).hexdigest()
    assert output.result.output_artifact_ids == (artifact.artifact_id,)
    assert json.loads(content)["language"] == "en"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_local_writer_creates_real_file_beneath_workspace(tmp_path) -> None:
    command, context = command_and_context()
    output = await handler(
        SimulatedPlanningProvider(), LocalPlanningArtifactWriter(tmp_path)
    ).execute(command, context)
    artifact = output.artifacts[0]
    target = tmp_path.joinpath(*artifact.relative_path.split("/"))
    assert target.is_file()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == artifact.sha256
    assert not list(target.parent.glob("*.tmp"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (PlanningProviderTimeoutError("timeout"), StageOutcome.FAILED_TRANSIENT),
        (PlanningProviderRateLimitError("rate"), StageOutcome.FAILED_TRANSIENT),
        (PlanningProviderAuthenticationError("auth"), StageOutcome.NEEDS_USER_ACTION),
    ],
)
async def test_handler_maps_provider_errors_without_partial_artifact(error, outcome) -> None:
    class FailingProvider(SimulatedPlanningProvider):
        async def generate_plan(self, request):
            raise error

    writer = InMemoryPlanningArtifactWriter()
    command, context = command_and_context()
    output = await handler(FailingProvider(), writer).execute(command, context)
    assert output.result.outcome is outcome
    assert output.artifacts == ()
    assert writer.contents == {}


@pytest.mark.asyncio
async def test_handler_rejects_unapproved_job_configuration() -> None:
    command, context = command_and_context({"provider": "override"})
    output = await handler(
        SimulatedPlanningProvider(), InMemoryPlanningArtifactWriter()
    ).execute(command, context)
    assert output.result.outcome is StageOutcome.NEEDS_USER_ACTION
    assert output.result.error_code == "planning_configuration_invalid"


@pytest.mark.asyncio
async def test_handler_preserves_command_context_and_cancelled_error() -> None:
    class CancellingProvider(SimulatedPlanningProvider):
        async def generate_plan(self, request):
            raise asyncio.CancelledError

    command, context = command_and_context()
    command_snapshot = command.model_dump_json()
    context_snapshot = context.model_dump_json()
    with pytest.raises(asyncio.CancelledError):
        await handler(
            CancellingProvider(), InMemoryPlanningArtifactWriter()
        ).execute(command, context)
    assert command.model_dump_json() == command_snapshot
    assert context.model_dump_json() == context_snapshot
