"""ScriptingHandler and safe artifact writer tests."""

import asyncio
import hashlib
from uuid import UUID

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.runtime.handlers import ScriptingHandler
from backend.src.production.scripting.artifact_writer import (
    InMemoryScriptingArtifactWriter,
    LocalScriptingArtifactWriter,
)
from backend.src.production.scripting.exceptions import (
    ProductionPlanChecksumError,
    ScriptingProviderAuthenticationError,
    ScriptingProviderTimeoutError,
)
from backend.src.production.scripting.ports import ReadProductionPlan
from backend.src.production.scripting.providers import SimulatedScriptingProvider
from backend.tests.unit.production.scripting.conftest import NOW, PLAN_ARTIFACT_ID


class Reader:
    def __init__(self, plan=None, error=None) -> None:
        self.plan = plan
        self.error = error
        self.calls = 0

    async def read_for_scripting(self, *, context):
        self.calls += 1
        if self.error:
            raise self.error
        return ReadProductionPlan(
            plan=self.plan,
            artifact_id=PLAN_ARTIFACT_ID,
            relative_path=f"production/{context.job_id}/planning/attempt-1/production-plan.json",
            sha256="a" * 64,
            size_bytes=100,
            schema_version=self.plan.schema_version,
            provider="orion-simulated",
            model_version="planning-simulator-v1",
            created_at=NOW,
            metadata={},
        )


class Provider(SimulatedScriptingProvider):
    def __init__(self, error=None) -> None:
        self.error = error
        self.calls = 0

    async def generate_script(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return await super().generate_script(request)


def handler(reader, provider, writer):
    return ScriptingHandler(
        plan_reader=reader,
        provider=provider,
        artifact_writer=writer,
        clock=lambda: NOW,
        uuid_factory=lambda: UUID("40000000-0000-4000-8000-000000000601"),
    )


@pytest.mark.asyncio
async def test_handler_reads_once_writes_exact_artifact_and_preserves_inputs(
    production_plan, scripting_command_context
) -> None:
    command, context = scripting_command_context
    reader = Reader(production_plan)
    provider = Provider()
    writer = InMemoryScriptingArtifactWriter()
    command_snapshot, context_snapshot = command.model_dump_json(), context.model_dump_json()
    output = await handler(reader, provider, writer).execute(command, context)
    artifact = output.artifacts[0]
    content = writer.contents[artifact.relative_path]
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert reader.calls == provider.calls == 1
    assert artifact.artifact_type is ArtifactType.PRODUCTION_SCRIPT
    assert artifact.mime_type == "application/json"
    assert artifact.relative_path.endswith("/scripting/attempt-1/production-script.json")
    assert artifact.size_bytes == len(content)
    assert artifact.sha256 == hashlib.sha256(content).hexdigest()
    assert artifact.metadata["source_plan_artifact_id"] == str(PLAN_ARTIFACT_ID)
    assert artifact.metadata["source_plan_sha256"] == "a" * 64
    assert output.result.output_artifact_ids == (artifact.artifact_id,)
    assert command.model_dump_json() == command_snapshot
    assert context.model_dump_json() == context_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (ProductionPlanChecksumError("bad checksum"), StageOutcome.NEEDS_USER_ACTION),
        (ScriptingProviderTimeoutError("timeout"), StageOutcome.FAILED_TRANSIENT),
        (ScriptingProviderAuthenticationError("auth"), StageOutcome.NEEDS_USER_ACTION),
    ],
)
async def test_handler_maps_failures_without_partial_artifacts(
    production_plan, scripting_command_context, error, outcome
) -> None:
    command, context = scripting_command_context
    writer = InMemoryScriptingArtifactWriter()
    if isinstance(error, ProductionPlanChecksumError):
        reader, provider = Reader(error=error), Provider()
    else:
        reader, provider = Reader(production_plan), Provider(error)
    output = await handler(reader, provider, writer).execute(command, context)
    assert output.result.outcome is outcome
    assert output.artifacts == ()
    assert writer.contents == {}
    if isinstance(error, ProductionPlanChecksumError):
        assert provider.calls == 0


@pytest.mark.asyncio
async def test_handler_propagates_cancelled_error(
    production_plan, scripting_command_context
) -> None:
    class CancellingProvider(Provider):
        async def generate_script(self, request):
            raise asyncio.CancelledError

    command, context = scripting_command_context
    with pytest.raises(asyncio.CancelledError):
        await handler(
            Reader(production_plan), CancellingProvider(), InMemoryScriptingArtifactWriter()
        ).execute(command, context)


@pytest.mark.asyncio
async def test_local_writer_is_atomic_idempotent_and_rejects_incompatible_content(
    tmp_path, scripting_request, scripting_command_context
) -> None:
    script = (await SimulatedScriptingProvider().generate_script(scripting_request)).script
    _, context = scripting_command_context
    writer = LocalScriptingArtifactWriter(tmp_path, max_script_bytes=100_000)
    first = await writer.write_script(context=context, script=script)
    assert await writer.write_script(context=context, script=script) == first
    target = tmp_path.joinpath(*first.relative_path.split("/"))
    assert target.is_file()
    assert not list(target.parent.glob("*.tmp"))
    changed = script.model_copy(update={"title": "Changed"})
    with pytest.raises(ValueError, match="incompatible"):
        await writer.write_script(context=context, script=changed)
    assert not list(target.parent.glob("*.tmp"))
