"""Handler, artifact integrity, recovery, and idempotency tests."""

import asyncio
import hashlib
from uuid import UUID

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.scene_planning.artifact_writer import (
    InMemoryScenePlanningArtifactWriter,
    LocalScenePlanningArtifactWriter,
)
from backend.src.production.scene_planning.exceptions import (
    ProductionScriptChecksumException,
    ScenePlanningProviderAuthenticationException,
    ScenePlanningProviderTimeoutException,
    ScenePlanningValidationException,
)
from backend.src.production.scene_planning.handler import ScenePlanningHandler
from backend.src.production.scene_planning.ports import ReadProductionScript
from backend.src.production.scene_planning.providers import (
    SimulatedScenePlanningProvider,
)
from backend.tests.unit.production.scene_planning.conftest import (
    NOW,
    SCRIPT_ARTIFACT_ID,
)


class Reader:
    def __init__(self, script=None, error=None, sha256="a" * 64) -> None:
        self.script = script
        self.error = error
        self.sha256 = sha256
        self.calls = 0

    async def read_for_scene_planning(self, *, context):
        self.calls += 1
        if self.error:
            raise self.error
        return ReadProductionScript(
            script=self.script,
            artifact_id=SCRIPT_ARTIFACT_ID,
            relative_path=(
                f"production/{context.job_id}/scripting/attempt-1/production-script.json"
            ),
            sha256=self.sha256,
            size_bytes=100,
            schema_version=self.script.schema_version,
            provider="orion-simulated",
            model_version="scripting-simulator-v1",
            created_at=NOW,
        )


class Provider(SimulatedScenePlanningProvider):
    def __init__(self, error=None) -> None:
        self.error = error
        self.calls = 0

    async def generate_scene_plan(self, script):
        self.calls += 1
        if self.error:
            raise self.error
        return await super().generate_scene_plan(script)


def handler(reader, provider, writer):
    return ScenePlanningHandler(
        script_reader=reader,
        provider=provider,
        artifact_writer=writer,
        clock=lambda: NOW,
        uuid_factory=lambda: UUID("40000000-0000-4000-8000-000000000701"),
    )


@pytest.mark.asyncio
async def test_handler_consumes_script_and_writes_exact_durable_artifact(
    production_script, scene_planning_command_context
) -> None:
    command, context = scene_planning_command_context
    reader = Reader(production_script)
    provider = Provider()
    writer = InMemoryScenePlanningArtifactWriter()
    command_snapshot = command.model_dump_json()
    context_snapshot = context.model_dump_json()
    output = await handler(reader, provider, writer).execute(command, context)
    artifact = output.artifacts[0]
    content = writer.contents[artifact.relative_path]
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert reader.calls == provider.calls == 1
    assert artifact.artifact_type is ArtifactType.PRODUCTION_SCENE_PLAN
    assert artifact.mime_type == "application/json"
    assert artifact.relative_path.endswith(
        "/scene_planning/attempt-1/scene-plan.json"
    )
    assert artifact.size_bytes == len(content)
    assert artifact.sha256 == hashlib.sha256(content).hexdigest()
    assert artifact.metadata["source_script_artifact_id"] == str(SCRIPT_ARTIFACT_ID)
    assert artifact.metadata["source_script_sha256"] == "a" * 64
    assert artifact.metadata["scene_count"] == 2
    assert output.result.output_artifact_ids == (artifact.artifact_id,)
    assert command.model_dump_json() == command_snapshot
    assert context.model_dump_json() == context_snapshot
    recovered = await writer.read_existing(context=context)
    assert recovered is not None
    assert recovered.scene_plan.source_script_sha256 == "a" * 64


@pytest.mark.asyncio
async def test_valid_existing_artifact_recovers_without_provider_call(
    production_script, scene_planning_command_context
) -> None:
    command, context = scene_planning_command_context
    reader = Reader(production_script)
    provider = Provider()
    writer = InMemoryScenePlanningArtifactWriter()
    first = await handler(reader, provider, writer).execute(command, context)
    content_snapshot = dict(writer.contents)
    second = await handler(reader, provider, writer).execute(command, context)
    assert first.result.outcome is second.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == 1
    assert second.result.metadata["recovered"] is True
    assert writer.contents == content_snapshot
    assert first.artifacts[0].sha256 == second.artifacts[0].sha256


@pytest.mark.asyncio
async def test_recovery_rejects_scene_plan_from_different_script_checksum(
    production_script, scene_planning_command_context
) -> None:
    command, context = scene_planning_command_context
    writer = InMemoryScenePlanningArtifactWriter()
    first_provider = Provider()
    await handler(Reader(production_script), first_provider, writer).execute(
        command,
        context,
    )
    replacement_provider = Provider()
    output = await handler(
        Reader(production_script, sha256="b" * 64),
        replacement_provider,
        writer,
    ).execute(command, context)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert replacement_provider.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (ProductionScriptChecksumException("checksum"), StageOutcome.NEEDS_USER_ACTION),
        (ScenePlanningProviderTimeoutException("timeout"), StageOutcome.FAILED_TRANSIENT),
        (
            ScenePlanningProviderAuthenticationException("auth"),
            StageOutcome.NEEDS_USER_ACTION,
        ),
    ],
)
async def test_handler_maps_errors_without_partial_artifacts(
    production_script, scene_planning_command_context, error, outcome
) -> None:
    command, context = scene_planning_command_context
    writer = InMemoryScenePlanningArtifactWriter()
    if isinstance(error, ProductionScriptChecksumException):
        reader, provider = Reader(error=error), Provider()
    else:
        reader, provider = Reader(production_script), Provider(error)
    output = await handler(reader, provider, writer).execute(command, context)
    assert output.result.outcome is outcome
    assert output.artifacts == ()
    assert writer.contents == {}
    if isinstance(error, ProductionScriptChecksumException):
        assert provider.calls == 0


@pytest.mark.asyncio
async def test_handler_propagates_cancelled_error(
    production_script, scene_planning_command_context
) -> None:
    class CancellingProvider(Provider):
        async def generate_scene_plan(self, script):
            raise asyncio.CancelledError

    command, context = scene_planning_command_context
    with pytest.raises(asyncio.CancelledError):
        await handler(
            Reader(production_script),
            CancellingProvider(),
            InMemoryScenePlanningArtifactWriter(),
        ).execute(command, context)


@pytest.mark.asyncio
async def test_local_writer_is_atomic_utf8_idempotent_and_rejects_corruption(
    tmp_path, production_script, scene_planning_command_context
) -> None:
    plan = (
        await SimulatedScenePlanningProvider().generate_scene_plan(production_script)
    ).scene_plan
    _, context = scene_planning_command_context
    writer = LocalScenePlanningArtifactWriter(
        tmp_path,
        max_scene_plan_bytes=100_000,
    )
    first = await writer.write_scene_plan(context=context, scene_plan=plan)
    assert await writer.write_scene_plan(context=context, scene_plan=plan) == first
    target = tmp_path.joinpath(*first.relative_path.split("/"))
    assert "Bogotá" in target.read_text(encoding="utf-8")
    assert not list(target.parent.glob("*.tmp"))
    target.write_bytes(b'{"schema_version":"1.0.0","schema_version":"1.0.0"}')
    with pytest.raises(ScenePlanningValidationException):
        await writer.read_existing(context=context)
    with pytest.raises(ScenePlanningValidationException, match="incompatible"):
        await writer.write_scene_plan(context=context, scene_plan=plan)
    assert not list(target.parent.glob("*.tmp"))
