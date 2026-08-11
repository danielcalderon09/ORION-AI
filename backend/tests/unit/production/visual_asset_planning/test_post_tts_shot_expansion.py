"""Post-TTS shot expansion, persistence, and fail-closed recovery tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome
from backend.src.production.composition.audio_first_duration_reader import (
    ReadDurableDurationResolution,
)
from backend.src.production.domain.duration_resolution import (
    DurableDurationResolution,
    ResolvedSceneDuration,
)
from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.image_acquisition.manifest_writer import (
    InMemoryImageAcquisitionManifestWriter,
)
from backend.src.production.image_acquisition.ports import ReadProductionVisualAssetPlan
from backend.src.production.runtime.context import StageContext
from backend.src.production.scene_planning.models import (
    ProductionCamera,
    ProductionScene,
    ProductionScenePlan,
    ProductionShot,
    ProductionTiming,
    ProductionTransition,
)
from backend.src.production.scripting.models import NarrativeRole, StoryBeat
from backend.src.production.visual_asset_planning.artifact_writer import (
    InMemoryVisualAssetPlanningArtifactWriter,
)
from backend.src.production.visual_asset_planning.handler import VisualAssetPlanningHandler
from backend.src.production.visual_asset_planning.ports import ReadProductionScenePlan
from backend.src.production.visual_asset_planning.providers import (
    SimulatedVisualAssetPlanningProvider,
)
from backend.src.production.visual_asset_planning.shot_expansion import (
    PostTtsShotExpansion,
    build_post_tts_shot_expansion,
)
from backend.tests.unit.production.image_acquisition.test_handler_manifest_recovery import (
    CountingProvider as CountingImageProvider,
)
from backend.tests.unit.production.image_acquisition.test_handler_manifest_recovery import (
    handler as image_handler,
)
from backend.tests.unit.production.image_acquisition.test_handler_manifest_recovery import (
    store as image_store,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000991")
SCENE_ARTIFACT_ID = UUID("20000000-0000-4000-8000-000000000991")
DURATION_ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000991")


def _scene_plan() -> ProductionScenePlan:
    return ProductionScenePlan(
        source_script_schema_version="1.0.0",
        source_script_sha256="a" * 64,
        title="El abismo",
        language="es",
        target_duration_seconds=8,
        scenes=(
            ProductionScene(
                scene_id="scene-001",
                scene_number=1,
                source_scene_number=1,
                title="La aparición",
                narration="Una criatura emerge desde el abismo.",
                objective="Revelar progresivamente una criatura bioluminiscente",
                story_beat=StoryBeat(
                    role=NarrativeRole.REVEAL,
                    information_introduced="Existe movimiento bajo la expedición",
                    prior_context="La expedición observa el abismo",
                    new_information="La criatura emerge",
                    open_question="Qué clase de criatura es",
                    transition_intent="Acercarse para revelar su forma",
                ),
                estimated_duration_seconds=8,
                shots=(
                    ProductionShot(
                        shot_id="scene-001-shot-001",
                        shot_number=1,
                        scene_number=1,
                        objective="Mostrar la aparición",
                        description="Una criatura emerge desde un abismo submarino oscuro",
                        camera=ProductionCamera(
                            framing="wide",
                            movement="dolly",
                            subject="criatura bioluminiscente",
                        ),
                        timing=ProductionTiming(
                            start_seconds=0,
                            duration_seconds=8,
                            end_seconds=8,
                        ),
                        transition=ProductionTransition(kind="none"),
                    ),
                ),
            ),
        ),
    )


def _resolution(*, sha: str = "b" * 64) -> ReadDurableDurationResolution:
    return ReadDurableDurationResolution(
        resolution=DurableDurationResolution(
            requested_target_duration_ms=8_000,
            scenes=(
                ResolvedSceneDuration(
                    scene_id="scene-001",
                    sequence_index=0,
                    planned_duration_ms=8_000,
                    actual_narration_duration_ms=9_000,
                    resolved_duration_ms=9_000,
                ),
            ),
            resolved_duration_ms=9_000,
            maximum_allowed_duration_ms=9_600,
            accepted=True,
        ),
        artifact_id=DURATION_ARTIFACT_ID,
        relative_path=(
            f"production/{JOB_ID}/generating_narration/attempt-1/"
            "speech-generation-manifest.json"
        ),
        sha256=sha,
    )


class SceneReader:
    async def read_for_visual_asset_planning(self, *, context):
        plan = _scene_plan()
        return ReadProductionScenePlan(
            scene_plan=plan,
            artifact_id=SCENE_ARTIFACT_ID,
            relative_path=(
                f"production/{context.job_id}/scene_planning/attempt-1/scene-plan.json"
            ),
            sha256="c" * 64,
            size_bytes=1_000,
            schema_version=plan.schema_version,
        )


class DurationReader:
    def __init__(self, *, sha: str = "b" * 64) -> None:
        self.sha = sha

    async def read_source_for_job(self, job_id):
        assert job_id == JOB_ID
        return _resolution(sha=self.sha)


class CountingProvider(SimulatedVisualAssetPlanningProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def generate_visual_asset_plan(self, request):
        self.calls += 1
        return await super().generate_visual_asset_plan(request)


class DuplicateIntentProvider(CountingProvider):
    async def generate_visual_asset_plan(self, request):
        response = await super().generate_visual_asset_plan(request)
        first, second = response.visual_asset_plan.assets
        duplicate = second.model_copy(update={"prompt": first.prompt})
        return response.model_copy(
            update={
                "visual_asset_plan": response.visual_asset_plan.model_copy(
                    update={"assets": (first, duplicate)}
                )
            }
        )


def _command_context(*, attempt: int = 1):
    command_id = UUID("40000000-0000-4000-8000-000000000991")
    command = StageCommand(
        command_id=command_id,
        job_id=JOB_ID,
        stage=ProductionStage.VISUAL_ASSET_PLANNING,
        attempt_number=attempt,
        idempotency_key=f"post-tts-expansion:{attempt}",
        configuration_snapshot={
            "configuration": {
                "visual_asset_planning": {
                    "images_per_shot": 1,
                    "target_width": 720,
                    "target_height": 1280,
                }
            }
        },
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=command_id,
        stage=command.stage,
        attempt_number=attempt,
        workspace_relative_path=(
            f"production/{JOB_ID}/visual_asset_planning/attempt-{attempt}"
        ),
        correlation_id=JOB_ID,
    )
    return command, context


def test_single_9s_narrative_scene_expands_to_distinct_6_plus_4_shots() -> None:
    expansion = build_post_tts_shot_expansion(
        job_id=JOB_ID,
        source_scene_plan_artifact_id=SCENE_ARTIFACT_ID,
        source_scene_plan_sha256="c" * 64,
        source_duration_artifact_id=DURATION_ARTIFACT_ID,
        source_duration_sha256="b" * 64,
        scene_plan=_scene_plan(),
        duration_resolution=_resolution().resolution,
        supported_provider_durations_seconds=(4, 6, 8),
    )
    assert tuple(item.provider_duration_seconds for item in expansion.allocations) == (6, 4)
    assert tuple(item.usable_duration_ms for item in expansion.allocations) == (6_000, 3_000)
    assert len({item.intent_key for item in expansion.allocations}) == 2
    assert tuple(
        shot.timing.duration_seconds
        for shot in expansion.expanded_scene_plan.scenes[0].shots
    ) == (6, 3)
    assert expansion.plan_fingerprint == expansion.calculated_fingerprint()


def test_historical_shot_expansion_shape_and_fingerprint_remain_compatible() -> None:
    expansion = build_post_tts_shot_expansion(
        job_id=JOB_ID,
        source_scene_plan_artifact_id=SCENE_ARTIFACT_ID,
        source_scene_plan_sha256="c" * 64,
        source_duration_artifact_id=DURATION_ARTIFACT_ID,
        source_duration_sha256="b" * 64,
        scene_plan=_scene_plan(),
        duration_resolution=_resolution().resolution,
        supported_provider_durations_seconds=(4, 6, 8),
    )
    historical_payload = expansion.model_dump(mode="json")

    assert all(
        "visual_mode" not in allocation
        and "motion_mode" not in allocation
        and "source_asset_id" not in allocation
        and "importance" not in allocation
        and "generation_priority" not in allocation
        for allocation in historical_payload["allocations"]
    )
    restored = PostTtsShotExpansion.model_validate(historical_payload)
    assert restored.model_dump(mode="json") == historical_payload
    assert restored.plan_fingerprint == restored.calculated_fingerprint()


@pytest.mark.asyncio
async def test_handler_persists_expansion_before_two_distinct_visual_assets() -> None:
    command, context = _command_context()
    provider = CountingProvider()
    writer = InMemoryVisualAssetPlanningArtifactWriter()
    ids = iter(
        (
            UUID("50000000-0000-4000-8000-000000000991"),
            UUID("60000000-0000-4000-8000-000000000991"),
        )
    )
    output = await VisualAssetPlanningHandler(
        scene_plan_reader=SceneReader(),
        duration_resolution_reader=DurationReader(),
        provider=provider,
        artifact_writer=writer,
        clock=lambda: NOW,
        uuid_factory=lambda: next(ids),
    ).execute(command, context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert provider.calls == 1
    assert tuple(item.artifact_type for item in output.artifacts) == (
        ArtifactType.PRODUCTION_SHOT_EXPANSION,
        ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN,
    )
    plan = next(
        item.visual_asset_plan
        for item in (
            await writer.read_existing(context=context),
        )
        if item is not None
    )
    assert len(plan.assets) == 2
    assert plan.assets[0].prompt != plan.assets[1].prompt
    assert plan.assets[0].source_shot_id != plan.assets[1].source_shot_id


@pytest.mark.asyncio
async def test_final_expanded_plan_drives_exactly_two_distinct_image_requests(
    tmp_path,
) -> None:
    command, context = _command_context()
    provider = CountingProvider()
    writer = InMemoryVisualAssetPlanningArtifactWriter()
    ids = iter(
        (
            UUID("50000000-0000-4000-8000-000000000993"),
            UUID("60000000-0000-4000-8000-000000000993"),
        )
    )
    visual_output = await VisualAssetPlanningHandler(
        scene_plan_reader=SceneReader(),
        duration_resolution_reader=DurationReader(),
        provider=provider,
        artifact_writer=writer,
        clock=lambda: NOW,
        uuid_factory=lambda: next(ids),
    ).execute(command, context)
    visual_artifact = next(
        item
        for item in visual_output.artifacts
        if item.artifact_type is ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN
    )
    written = await writer.read_existing(context=context)
    assert written is not None
    source = ReadProductionVisualAssetPlan(
        visual_asset_plan=written.visual_asset_plan,
        job_id=JOB_ID,
        artifact_id=visual_artifact.artifact_id,
        relative_path=visual_artifact.relative_path,
        sha256=visual_artifact.sha256,
        size_bytes=visual_artifact.size_bytes,
        schema_version=written.visual_asset_plan.schema_version,
        provider=visual_artifact.provider,
        model_version=visual_artifact.model_version,
        created_at=NOW,
    )
    image_command_id = UUID("70000000-0000-4000-8000-000000000993")
    image_command = StageCommand(
        command_id=image_command_id,
        job_id=JOB_ID,
        stage=ProductionStage.ACQUIRING_ASSETS,
        attempt_number=1,
        idempotency_key="post-tts-images",
        input_artifact_ids=(visual_artifact.artifact_id,),
        configuration_snapshot={},
        created_at=NOW,
    )
    image_context = StageContext(
        job_id=JOB_ID,
        command_id=image_command_id,
        stage=ProductionStage.ACQUIRING_ASSETS,
        attempt_number=1,
        input_artifact_ids=image_command.input_artifact_ids,
        workspace_relative_path=f"production/{JOB_ID}/acquiring_assets/attempt-1",
        correlation_id=JOB_ID,
    )
    image_provider = CountingImageProvider()
    image_output = await image_handler(
        source=source,
        provider=image_provider,
        manifest_writer=InMemoryImageAcquisitionManifestWriter(),
        binary_store=image_store(tmp_path),
    ).execute(image_command, image_context)

    assert image_output.result.outcome is StageOutcome.SUCCEEDED
    assert image_provider.calls == [
        "asset-s001-q001-v001",
        "asset-s001-q002-v001",
    ]
    assert len(
        [
            item
            for item in image_output.artifacts
            if item.artifact_type is ArtifactType.SOURCE_IMAGE
        ]
    ) == 2


@pytest.mark.asyncio
async def test_duration_identity_drift_fails_before_visual_provider() -> None:
    command, context = _command_context()
    writer = InMemoryVisualAssetPlanningArtifactWriter()
    first_provider = CountingProvider()
    ids = iter(
        (
            UUID("50000000-0000-4000-8000-000000000992"),
            UUID("60000000-0000-4000-8000-000000000992"),
        )
    )
    first = VisualAssetPlanningHandler(
        scene_plan_reader=SceneReader(),
        duration_resolution_reader=DurationReader(),
        provider=first_provider,
        artifact_writer=writer,
        clock=lambda: NOW,
        uuid_factory=lambda: next(ids),
    )
    assert (await first.execute(command, context)).result.outcome is StageOutcome.SUCCEEDED

    retry_command, retry_context = _command_context(attempt=2)
    retry_provider = CountingProvider()
    retry = VisualAssetPlanningHandler(
        scene_plan_reader=SceneReader(),
        duration_resolution_reader=DurationReader(sha="d" * 64),
        provider=retry_provider,
        artifact_writer=writer,
        clock=lambda: NOW,
        uuid_factory=lambda: UUID("70000000-0000-4000-8000-000000000992"),
    )
    output = await retry.execute(retry_command, retry_context)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert retry_provider.calls == 0


@pytest.mark.asyncio
async def test_duplicate_consecutive_visual_prompt_fails_before_image_acquisition() -> None:
    command, context = _command_context()
    provider = DuplicateIntentProvider()
    output = await VisualAssetPlanningHandler(
        scene_plan_reader=SceneReader(),
        duration_resolution_reader=DurationReader(),
        provider=provider,
        artifact_writer=InMemoryVisualAssetPlanningArtifactWriter(),
        clock=lambda: NOW,
        uuid_factory=lambda: UUID("80000000-0000-4000-8000-000000000993"),
    ).execute(command, context)

    assert provider.calls == 1
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert output.result.output_artifact_ids == ()
