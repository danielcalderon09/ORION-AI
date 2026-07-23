"""Handler, writer, provenance, recovery, and idempotency tests."""

import asyncio
import hashlib

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.visual_asset_planning.artifact_writer import (
    InMemoryVisualAssetPlanningArtifactWriter,
    LocalVisualAssetPlanningArtifactWriter,
)
from backend.src.production.visual_asset_planning.exceptions import (
    ProductionScenePlanChecksumException,
    VisualAssetPlanningProviderAuthenticationException,
    VisualAssetPlanningProviderTimeoutException,
    VisualAssetPlanningValidationException,
)
from backend.src.production.visual_asset_planning.handler import (
    VisualAssetPlanningHandler,
)
from backend.src.production.visual_asset_planning.ports import (
    ReadProductionScenePlan,
)
from backend.src.production.visual_asset_planning.providers import (
    SimulatedVisualAssetPlanningProvider,
)
from backend.tests.unit.production.visual_asset_planning.conftest import (
    NOW,
    OUTPUT_ARTIFACT_ID,
    SCENE_PLAN_ARTIFACT_ID,
)


class Reader:
    def __init__(self, scene_plan=None, error=None, sha256="c" * 64) -> None:
        self.scene_plan = scene_plan
        self.error = error
        self.sha256 = sha256
        self.calls = 0

    async def read_for_visual_asset_planning(self, *, context):
        self.calls += 1
        if self.error:
            raise self.error
        return ReadProductionScenePlan(
            scene_plan=self.scene_plan,
            artifact_id=SCENE_PLAN_ARTIFACT_ID,
            relative_path=(f"production/{context.job_id}/scene_planning/attempt-1/scene-plan.json"),
            sha256=self.sha256,
            size_bytes=100,
            schema_version=self.scene_plan.schema_version,
            provider="orion-simulated",
            model_version="scene-planning-simulator-v1",
            created_at=NOW,
            metadata={"shot_count": 4},
        )


class Provider(SimulatedVisualAssetPlanningProvider):
    def __init__(self, error=None) -> None:
        self.error = error
        self.calls = 0

    async def generate_visual_asset_plan(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return await super().generate_visual_asset_plan(request)


def handler(reader, provider, writer):
    return VisualAssetPlanningHandler(
        scene_plan_reader=reader,
        provider=provider,
        artifact_writer=writer,
        clock=lambda: NOW,
        uuid_factory=lambda: OUTPUT_ARTIFACT_ID,
    )


@pytest.mark.asyncio
async def test_handler_writes_exact_durable_artifact_and_safe_metadata(
    production_scene_plan,
    visual_asset_command_context,
) -> None:
    command, context = visual_asset_command_context
    reader = Reader(production_scene_plan)
    provider = Provider()
    writer = InMemoryVisualAssetPlanningArtifactWriter()
    command_snapshot = command.model_dump_json()
    context_snapshot = context.model_dump_json()
    output = await handler(reader, provider, writer).execute(command, context)
    artifact = output.artifacts[0]
    content = writer.contents[artifact.relative_path]
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert reader.calls == provider.calls == 1
    assert artifact.artifact_type is ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN
    assert artifact.mime_type == "application/json"
    assert artifact.relative_path.endswith(
        "/visual_asset_planning/attempt-1/visual-asset-plan.json"
    )
    assert artifact.size_bytes == len(content)
    assert artifact.sha256 == hashlib.sha256(content).hexdigest()
    assert artifact.metadata["source_scene_plan_artifact_id"] == str(SCENE_PLAN_ARTIFACT_ID)
    assert artifact.metadata["source_scene_plan_sha256"] == "c" * 64
    assert artifact.metadata["asset_count"] == 4
    assert artifact.metadata["shot_count"] == 4
    assert artifact.metadata["deterministic"] is True
    assert artifact.metadata["simulated"] is True
    assert "api_key" not in artifact.model_dump_json()
    assert output.result.output_artifact_ids == (artifact.artifact_id,)
    assert command.model_dump_json() == command_snapshot
    assert context.model_dump_json() == context_snapshot


@pytest.mark.asyncio
async def test_valid_existing_artifact_recovers_without_provider_and_is_idempotent(
    production_scene_plan,
    visual_asset_command_context,
) -> None:
    command, context = visual_asset_command_context
    source_reader = Reader(production_scene_plan)
    provider = Provider()
    writer = InMemoryVisualAssetPlanningArtifactWriter()
    first = await handler(source_reader, provider, writer).execute(command, context)
    snapshot = dict(writer.contents)
    second = await handler(source_reader, provider, writer).execute(command, context)
    assert first.result.outcome is second.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == 1
    assert second.result.metadata["recovered"] is True
    assert writer.contents == snapshot
    assert first.artifacts[0].sha256 == second.artifacts[0].sha256


@pytest.mark.asyncio
async def test_recovery_rejects_changed_source_and_corruption_without_provider(
    production_scene_plan,
    visual_asset_command_context,
) -> None:
    command, context = visual_asset_command_context
    writer = InMemoryVisualAssetPlanningArtifactWriter()
    await handler(Reader(production_scene_plan), Provider(), writer).execute(
        command,
        context,
    )
    replacement = Provider()
    changed = await handler(
        Reader(production_scene_plan, sha256="d" * 64),
        replacement,
        writer,
    ).execute(command, context)
    assert changed.result.outcome is StageOutcome.FAILED_PERMANENT
    assert replacement.calls == 0
    path = next(iter(writer.contents))
    writer.contents[path] = b"{"
    corrupted = await handler(
        Reader(production_scene_plan),
        replacement,
        writer,
    ).execute(command, context)
    assert corrupted.result.outcome is StageOutcome.FAILED_PERMANENT
    assert replacement.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (
            ProductionScenePlanChecksumException("checksum"),
            StageOutcome.FAILED_PERMANENT,
        ),
        (
            VisualAssetPlanningProviderTimeoutException("timeout"),
            StageOutcome.FAILED_TRANSIENT,
        ),
        (
            VisualAssetPlanningProviderAuthenticationException("auth"),
            StageOutcome.FAILED_PERMANENT,
        ),
    ],
)
async def test_handler_maps_errors_without_partial_artifacts(
    production_scene_plan,
    visual_asset_command_context,
    error,
    outcome,
) -> None:
    command, context = visual_asset_command_context
    writer = InMemoryVisualAssetPlanningArtifactWriter()
    if isinstance(error, ProductionScenePlanChecksumException):
        source_reader, provider = Reader(error=error), Provider()
    else:
        source_reader, provider = Reader(production_scene_plan), Provider(error)
    output = await handler(source_reader, provider, writer).execute(command, context)
    assert output.result.outcome is outcome
    assert output.artifacts == ()
    assert writer.contents == {}
    if isinstance(error, ProductionScenePlanChecksumException):
        assert provider.calls == 0


@pytest.mark.asyncio
async def test_handler_propagates_cancelled_error(
    production_scene_plan,
    visual_asset_command_context,
) -> None:
    class CancellingProvider(Provider):
        async def generate_visual_asset_plan(self, request):
            raise asyncio.CancelledError

    command, context = visual_asset_command_context
    with pytest.raises(asyncio.CancelledError):
        await handler(
            Reader(production_scene_plan),
            CancellingProvider(),
            InMemoryVisualAssetPlanningArtifactWriter(),
        ).execute(command, context)


@pytest.mark.asyncio
async def test_local_writer_is_atomic_utf8_and_attempt_paths_do_not_collide(
    tmp_path,
    production_scene_plan,
    visual_asset_command_context,
) -> None:
    command, context = visual_asset_command_context
    provider = Provider()
    response = await provider.generate_visual_asset_plan(
        _request_from(command, context, production_scene_plan)
    )
    plan = response.visual_asset_plan.model_copy(
        update={
            "source_scene_plan_artifact_id": SCENE_PLAN_ARTIFACT_ID,
            "source_scene_plan_sha256": "c" * 64,
        }
    )
    writer = LocalVisualAssetPlanningArtifactWriter(
        tmp_path,
        max_artifact_bytes=1_000_000,
    )
    first = await writer.write_visual_asset_plan(
        context=context,
        visual_asset_plan=plan,
    )
    assert (
        await writer.write_visual_asset_plan(
            context=context,
            visual_asset_plan=plan,
        )
        == first
    )
    target = tmp_path.joinpath(*first.relative_path.split("/"))
    assert "Bogotá" in target.read_text(encoding="utf-8")
    assert first.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert not list(target.parent.glob("*.tmp"))
    incompatible = plan.model_copy(update={"title": "Otro título"})
    with pytest.raises(VisualAssetPlanningValidationException, match="incompatible"):
        await writer.write_visual_asset_plan(
            context=context,
            visual_asset_plan=incompatible,
        )
    retry_context = context.model_copy(
        update={
            "attempt_number": 2,
            "workspace_relative_path": (
                f"production/{context.job_id}/visual_asset_planning/attempt-2"
            ),
        }
    )
    retry = await writer.write_visual_asset_plan(
        context=retry_context,
        visual_asset_plan=plan,
    )
    assert retry.relative_path != first.relative_path
    assert target.exists()


def _request_from(command, context, scene_plan):
    from backend.src.production.visual_asset_planning.configuration import (
        visual_asset_planning_configuration_from_snapshot,
    )
    from backend.src.production.visual_asset_planning.ports import (
        VisualAssetPlanningProviderRequest,
    )

    return VisualAssetPlanningProviderRequest(
        job_id=command.job_id,
        command_id=command.command_id,
        correlation_id=context.correlation_id,
        attempt_number=command.attempt_number,
        scene_plan=scene_plan,
        configuration=visual_asset_planning_configuration_from_snapshot(
            command.configuration_snapshot
        ),
    )
