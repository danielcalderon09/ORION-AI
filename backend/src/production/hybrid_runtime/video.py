"""Hybrid video stage adapter and deterministic visual-track realization."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from PIL import Image

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType, ProductionStage
from backend.src.production.hybrid_runtime.assets import (
    ACQUISITION_FILENAME,
    HybridRuntimeFilesystem,
)
from backend.src.production.hybrid_runtime.planning import BUDGET_FILENAME, STRATEGY_FILENAME
from backend.src.production.image_acquisition.hybrid_acquisition import (
    deserialize_hybrid_acquisition_manifest,
)
from backend.src.production.media_composition.domain.hybrid import (
    HybridImageMotionCompositionPlan,
    HybridVisualAssetReference,
    HybridVisualSegmentInput,
    HybridVisualSourceKind,
    build_hybrid_image_motion_plan,
    serialize_hybrid_image_motion_plan,
)
from backend.src.production.planning.aggregate_visual_budget import (
    deserialize_aggregate_visual_budget_plan,
)
from backend.src.production.planning.visual_strategy import (
    deserialize_hybrid_visual_strategy_plan,
)
from backend.src.production.rendering.image_motion import (
    HybridFFmpegExecutionPlan,
    HybridImageMotionRenderResult,
    LocalHybridImageMotionRenderer,
    build_hybrid_ffmpeg_execution_plan,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.hybrid_generation import (
    GeneratedHybridVideoPayload,
    HybridGeneratedVideoStore,
    HybridRemoteVideoStatus,
    HybridVideoGenerationCoordinator,
    HybridVideoGenerationEntry,
    HybridVideoGenerationError,
    HybridVideoGenerationManifest,
    HybridVideoGenerationManifestWriter,
    HybridVideoGenerationProvider,
    HybridVideoGenerationSource,
    HybridVideoProviderRequest,
    StoredHybridVideoAsset,
    deserialize_hybrid_video_manifest,
    serialize_hybrid_video_manifest,
)
from backend.src.production.video_clip_generation.ports import (
    VideoClipGenerationProvider,
    VideoClipProviderRequest,
)
from backend.src.production.video_clip_generation.retry_budget import (
    FilesystemVideoRetryBudgetAuthorizationStore,
    VideoRetryBudgetAuthorizationError,
)

VIDEO_MANIFEST_FILENAME = "hybrid-video-generation-manifest.json"
COMPOSITION_FILENAME = "hybrid-image-motion-composition-plan.json"
EXECUTION_FILENAME = "hybrid-ffmpeg-execution-plan.json"
RENDER_RESULT_FILENAME = "hybrid-image-motion-render-result.json"


class FilesystemHybridVideoManifestWriter(HybridVideoGenerationManifestWriter):
    def __init__(self, filesystem: HybridRuntimeFilesystem, context: StageContext) -> None:
        self._fs = filesystem
        self._context = context
        self._target = filesystem.resolve(f"{context.workspace_relative_path}/{VIDEO_MANIFEST_FILENAME}")

    async def read(self) -> HybridVideoGenerationManifest | None:
        if self._target.exists():
            return deserialize_hybrid_video_manifest(await asyncio.to_thread(self._target.read_bytes))
        try:
            previous = self._fs.latest(
                self._context.job_id,
                "generating_video_clips",
                VIDEO_MANIFEST_FILENAME,
            )
        except ValueError:
            return None
        if previous == self._target:
            return None
        return deserialize_hybrid_video_manifest(await asyncio.to_thread(previous.read_bytes))

    async def create(self, manifest: HybridVideoGenerationManifest) -> None:
        if await self.read() is not None:
            raise HybridVideoGenerationError("hybrid video manifest already exists")
        await asyncio.to_thread(
            self._fs.atomic_replace,
            self._target,
            serialize_hybrid_video_manifest(manifest),
        )

    async def checkpoint(
        self,
        previous: HybridVideoGenerationManifest,
        current: HybridVideoGenerationManifest,
    ) -> None:
        if await self.read() != previous:
            raise HybridVideoGenerationError("hybrid video manifest changed concurrently")
        await asyncio.to_thread(
            self._fs.atomic_replace,
            self._target,
            serialize_hybrid_video_manifest(current),
        )

    @property
    def relative_path(self) -> str:
        return self._fs.relative(self._target)


class LegacyHybridVideoProviderAdapter(HybridVideoGenerationProvider):
    """Adapt the existing safe provider boundary without changing its semantics."""

    def __init__(
        self,
        *,
        filesystem: HybridRuntimeFilesystem,
        provider: VideoClipGenerationProvider,
        configuration: VideoClipGenerationConfiguration,
        command: StageCommand,
        context: StageContext,
    ) -> None:
        self._fs = filesystem
        self._provider = provider
        self._configuration = configuration
        self._command = command
        self._context = context

    async def generate_video(
        self, request: HybridVideoProviderRequest
    ) -> GeneratedHybridVideoPayload:
        source = self._fs.resolve(request.source_image_storage_reference)
        content = await asyncio.to_thread(source.read_bytes)
        if hashlib.sha256(content).hexdigest() != request.source_image_sha256:
            raise HybridVideoGenerationError("hybrid first-frame checksum drifted")
        with Image.open(source) as image:
            width, height = image.size
            image.verify()
        output_width, output_height = self._configuration.output_dimensions(width, height)
        scene_id = request.shot_id.rsplit("-shot-", 1)[0]
        legacy = VideoClipProviderRequest(
            job_id=request.job_id,
            command_id=self._command.command_id,
            correlation_id=self._context.correlation_id,
            attempt_number=self._context.attempt_number,
            visual_asset_id=request.visual_asset_id,
            scene_id=scene_id,
            shot_id=request.shot_id,
            clip_index=1,
            visual_intent_sha256=hashlib.sha256(request.visual_asset_id.encode()).hexdigest(),
            source_image_artifact_id=uuid5(
                NAMESPACE_URL, f"orion:{request.source_image_local_asset_id}"
            ),
            source_image_sha256=request.source_image_sha256,
            source_image_mime_type=request.source_image_mime_type,
            source_image_size_bytes=len(content),
            source_image_width=width,
            source_image_height=height,
            source_role="primary",
            source_metadata={
                "hybrid": True,
                "strategy_fingerprint": request.strategy_fingerprint,
                "budget_fingerprint": request.budget_fingerprint,
                "acquisition_fingerprint": request.acquisition_fingerprint,
            },
            source_image_content=content,
            duration_seconds=request.provider_duration_seconds,
            frame_rate=self._configuration.frame_rate,
            width=output_width,
            height=output_height,
            configuration=self._configuration,
            fingerprint=request.request_identity,
        )
        response = await self._provider.generate_clip(legacy)
        payload = response.clips[0]
        remote_id = response.request_id or hashlib.sha256(
            f"{request.request_identity}:completed".encode()
        ).hexdigest()[:32]
        return GeneratedHybridVideoPayload(
            content=payload.content,
            mime_type="video/mp4",
            width=output_width,
            height=output_height,
            duration_ms=request.provider_duration_seconds * 1_000,
            provider=response.provider,
            model=response.reported_model,
            remote_generation_id=remote_id,
            remote_status=HybridRemoteVideoStatus.COMPLETED,
            download_identity=hashlib.sha256(payload.content).hexdigest(),
            reported_cost_usd=response.cost_usd,
        )


class FilesystemHybridGeneratedVideoStore(HybridGeneratedVideoStore):
    def __init__(self, filesystem: HybridRuntimeFilesystem) -> None:
        self._fs = filesystem

    async def store_generated(
        self,
        *,
        job_id: UUID,
        entry: HybridVideoGenerationEntry,
        payload: GeneratedHybridVideoPayload,
    ) -> StoredHybridVideoAsset:
        identity = entry.provider_request_identity
        if identity is None:
            raise HybridVideoGenerationError("generated hybrid video lacks request identity")
        relative = f"production/{job_id}/hybrid-assets/videos/{identity[:24]}.mp4"
        target = self._fs.resolve(relative)
        if target.exists():
            if target.read_bytes() != payload.content:
                raise HybridVideoGenerationError("generated hybrid video drifted during recovery")
        else:
            await asyncio.to_thread(self._fs.atomic_replace, target, payload.content)
        return StoredHybridVideoAsset(
            local_asset_id=f"hybrid-video-{identity[:24]}",
            sha256=hashlib.sha256(payload.content).hexdigest(),
            mime_type="video/mp4",
            width=payload.width,
            height=payload.height,
            duration_ms=payload.duration_ms,
            storage_reference=relative,
            provenance="orion-hybrid-generated-video-v1",
        )


class HybridVideoGenerationStageHandler:
    supported_stages = frozenset({ProductionStage.GENERATING_VIDEO_CLIPS})

    def __init__(
        self,
        *,
        workspace_root: Path,
        provider: VideoClipGenerationProvider,
        configuration: VideoClipGenerationConfiguration,
        renderer: LocalHybridImageMotionRenderer,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
        retry_budget_store: FilesystemVideoRetryBudgetAuthorizationStore | None = None,
        maximum_video_job_cost_usd: Decimal | None = None,
    ) -> None:
        self._fs = HybridRuntimeFilesystem(workspace_root)
        self._provider = provider
        self._configuration = configuration
        self._renderer = renderer
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._retry_budget_store = retry_budget_store
        self._maximum_video_job_cost_usd = maximum_video_job_cost_usd

    async def execute(
        self, command: StageCommand, context: StageContext
    ) -> StageExecutionOutput:
        started = self._aware_now()
        try:
            if context.attempt_number > 1:
                if (
                    self._retry_budget_store is None
                    or self._maximum_video_job_cost_usd is None
                ):
                    raise VideoRetryBudgetAuthorizationError(
                        "hybrid video recovery budget guard is unavailable"
                    )
                await self._retry_budget_store.require_for_recovery(
                    job_id=command.job_id,
                    target_stage_attempt=context.attempt_number,
                    current_settings_video_job_ceiling_usd=(
                        self._maximum_video_job_cost_usd
                    ),
                )
            source = self._read_source(command.job_id)
            writer = FilesystemHybridVideoManifestWriter(self._fs, context)
            coordinator = HybridVideoGenerationCoordinator(
                provider=LegacyHybridVideoProviderAdapter(
                    filesystem=self._fs,
                    provider=self._provider,
                    configuration=self._configuration,
                    command=command,
                    context=context,
                ),
                store=FilesystemHybridGeneratedVideoStore(self._fs),
                manifest_writer=writer,
            )
            manifest = await coordinator.execute(source)
            composition, execution, render = await self._realize_visual_track(
                command=command,
                context=context,
                source=source,
                manifest=manifest,
            )
            manifest_content = serialize_hybrid_video_manifest(manifest)
            artifacts = (
                self._json_artifact(
                    command,
                    ArtifactType.HYBRID_VIDEO_GENERATION_MANIFEST,
                    writer.relative_path,
                    manifest_content,
                    manifest.fingerprint,
                ),
                self._json_artifact(
                    command,
                    ArtifactType.HYBRID_IMAGE_MOTION_COMPOSITION_PLAN,
                    f"{context.workspace_relative_path}/{COMPOSITION_FILENAME}",
                    serialize_hybrid_image_motion_plan(composition),
                    composition.fingerprint,
                ),
                self._json_artifact(
                    command,
                    ArtifactType.HYBRID_FFMPEG_EXECUTION_PLAN,
                    f"{context.workspace_relative_path}/{EXECUTION_FILENAME}",
                    _serialize_model(execution),
                    execution.fingerprint,
                ),
                self._json_artifact(
                    command,
                    ArtifactType.HYBRID_IMAGE_MOTION_RENDER_RESULT,
                    f"{context.workspace_relative_path}/{RENDER_RESULT_FILENAME}",
                    _serialize_model(render),
                    execution.fingerprint,
                ),
                Artifact(
                    artifact_id=self._uuid_factory(),
                    job_id=command.job_id,
                    artifact_type=ArtifactType.SOURCE_VIDEO_CLIP,
                    relative_path=render.output_relative_path,
                    mime_type="video/mp4",
                    status=ArtifactStatus.READY,
                    size_bytes=render.size_bytes,
                    sha256=render.output_sha256,
                    provider="orion-local-hybrid",
                    model_version="hybrid-image-motion-v1",
                    metadata={
                        "hybrid_visual_track": True,
                        "duration_ms": render.duration_ms,
                        "width": render.width,
                        "height": render.height,
                        "frame_rate": render.frame_rate,
                        "composition_fingerprint": composition.fingerprint,
                    },
                ),
            )
            result = StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.SUCCEEDED,
                started_at=started,
                finished_at=self._aware_now(),
                progress_percent=100,
                output_artifact_ids=tuple(item.artifact_id for item in artifacts),
                metadata={
                    "handler": type(self).__name__,
                    "hybrid": True,
                    "video_requests": source.budget_plan.video_requests,
                    "purchased_video_seconds": source.budget_plan.purchased_video_seconds,
                    "video_manifest_fingerprint": manifest.fingerprint,
                    "composition_fingerprint": composition.fingerprint,
                },
            )
            return StageExecutionOutput(result=result, artifacts=artifacts)
        except VideoRetryBudgetAuthorizationError as exc:
            return StageExecutionOutput(
                result=StageResult(
                    command_id=command.command_id,
                    job_id=command.job_id,
                    stage=command.stage,
                    outcome=StageOutcome.FAILED_PERMANENT,
                    started_at=started,
                    finished_at=self._aware_now(),
                    progress_percent=0,
                    error_code="hybrid_video_retry_budget_not_authorized",
                    error_message=str(exc),
                    metadata={"handler": type(self).__name__},
                )
            )
        except (HybridVideoGenerationError, OSError, ValueError) as exc:
            transient = (
                "transient hybrid video failure" in str(exc)
                or "hybrid FFmpeg render failed" in str(exc)
            )
            return StageExecutionOutput(
                result=StageResult(
                    command_id=command.command_id,
                    job_id=command.job_id,
                    stage=command.stage,
                    outcome=(
                        StageOutcome.FAILED_TRANSIENT
                        if transient
                        else StageOutcome.FAILED_PERMANENT
                    ),
                    started_at=started,
                    finished_at=self._aware_now(),
                    progress_percent=0,
                    error_code=(
                        "hybrid_video_provider_transient"
                        if transient
                        else "hybrid_video_generation_failed"
                    ),
                    error_message=str(exc),
                    retry_after_seconds=1.0 if transient else None,
                    metadata={"handler": type(self).__name__},
                )
            )

    def _read_source(self, job_id: UUID) -> HybridVideoGenerationSource:
        strategy = deserialize_hybrid_visual_strategy_plan(
            self._fs.latest(job_id, "visual_asset_planning", STRATEGY_FILENAME).read_bytes()
        )
        budget = deserialize_aggregate_visual_budget_plan(
            self._fs.latest(job_id, "visual_asset_planning", BUDGET_FILENAME).read_bytes()
        )
        acquisition = deserialize_hybrid_acquisition_manifest(
            self._fs.latest(job_id, "acquiring_assets", ACQUISITION_FILENAME).read_bytes()
        )
        return HybridVideoGenerationSource(
            strategy_plan=strategy,
            budget_plan=budget,
            acquisition_manifest=acquisition,
        )

    async def _realize_visual_track(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        source: HybridVideoGenerationSource,
        manifest: HybridVideoGenerationManifest,
    ) -> tuple[
        HybridImageMotionCompositionPlan,
        HybridFFmpegExecutionPlan,
        HybridImageMotionRenderResult,
    ]:
        strategy_by_shot = {item.shot_id: item for item in source.strategy_plan.shots}
        first_asset = manifest.entries[0].resolved_asset
        if first_asset is None or first_asset.width is None or first_asset.height is None:
            raise HybridVideoGenerationError("hybrid visual geometry is unavailable")
        output_width, output_height = _hybrid_output_dimensions(
            first_asset.width,
            first_asset.height,
            resolution=self._configuration.resolution,
        )
        inputs: list[HybridVisualSegmentInput] = []
        for entry in manifest.entries:
            asset = entry.resolved_asset
            if asset is None:
                raise HybridVideoGenerationError("hybrid visual asset is unresolved")
            target = self._fs.resolve(asset.storage_reference)
            size = target.stat().st_size
            kind = (
                HybridVisualSourceKind.IMAGE
                if asset.mime_type.startswith("image/")
                else HybridVisualSourceKind.VIDEO
            )
            inputs.append(
                HybridVisualSegmentInput(
                    shot_id=entry.shot_id,
                    visual_mode=entry.visual_mode,
                    motion_mode=strategy_by_shot[entry.shot_id].motion_mode,
                    usable_duration_ms=entry.usable_duration_ms,
                    asset=HybridVisualAssetReference(
                        asset_id=asset.local_asset_id,
                        relative_path=asset.storage_reference,
                        sha256=asset.sha256,
                        mime_type=asset.mime_type,
                        size_bytes=size,
                        width=asset.width or output_width,
                        height=asset.height or output_height,
                        source_kind=kind,
                        source_duration_ms=(asset.duration_ms if kind is HybridVisualSourceKind.VIDEO else None),
                    ),
                )
            )
        composition = build_hybrid_image_motion_plan(
            job_id=command.job_id,
            strategy_fingerprint=source.strategy_plan.fingerprint,
            acquisition_fingerprint=source.acquisition_manifest.fingerprint,
            inputs=tuple(inputs),
            output_width=output_width,
            output_height=output_height,
            frame_rate=self._configuration.frame_rate,
        )
        composition_content = serialize_hybrid_image_motion_plan(composition)
        composition_path = self._fs.resolve(
            f"{context.workspace_relative_path}/{COMPOSITION_FILENAME}"
        )
        self._write_or_verify(composition_path, composition_content)
        output_relative = (
            f"production/{command.job_id}/hybrid-assets/render/"
            f"{composition.fingerprint[:24]}.mp4"
        )
        execution = build_hybrid_ffmpeg_execution_plan(
            composition,
            output_relative_path=output_relative,
        )
        execution_path = self._fs.resolve(f"{context.workspace_relative_path}/{EXECUTION_FILENAME}")
        self._write_or_verify(execution_path, _serialize_model(execution))
        render_path = self._fs.resolve(f"{context.workspace_relative_path}/{RENDER_RESULT_FILENAME}")
        if not render_path.exists():
            try:
                previous_result = self._fs.latest(
                    command.job_id,
                    "generating_video_clips",
                    RENDER_RESULT_FILENAME,
                )
            except ValueError:
                previous_result = None
            if previous_result is not None and previous_result != render_path:
                recovered = HybridImageMotionRenderResult.model_validate_json(
                    previous_result.read_bytes()
                )
                if recovered.execution_fingerprint != execution.fingerprint:
                    raise HybridVideoGenerationError("hybrid render recovery plan drifted")
                output = self._fs.resolve(recovered.output_relative_path)
                content = output.read_bytes()
                if hashlib.sha256(content).hexdigest() != recovered.output_sha256:
                    raise HybridVideoGenerationError("hybrid render output drifted")
                self._write_or_verify(render_path, _serialize_model(recovered))
                return composition, execution, recovered
        if render_path.exists():
            render = HybridImageMotionRenderResult.model_validate_json(render_path.read_bytes())
            output = self._fs.resolve(render.output_relative_path)
            content = output.read_bytes()
            if hashlib.sha256(content).hexdigest() != render.output_sha256:
                raise HybridVideoGenerationError("hybrid render output drifted")
            return composition, execution, render
        render = await self._renderer.render(composition=composition, execution=execution)
        self._write_or_verify(render_path, _serialize_model(render))
        return composition, execution, render

    def _write_or_verify(self, path: Path, content: bytes) -> None:
        if path.exists():
            if path.read_bytes() != content:
                raise HybridVideoGenerationError("hybrid render plan drifted")
            return
        self._fs.atomic_replace(path, content)

    def _json_artifact(
        self,
        command: StageCommand,
        artifact_type: ArtifactType,
        relative_path: str,
        content: bytes,
        fingerprint: str,
    ) -> Artifact:
        return Artifact(
            artifact_id=self._uuid_factory(),
            job_id=command.job_id,
            artifact_type=artifact_type,
            relative_path=relative_path,
            mime_type="application/json",
            status=ArtifactStatus.READY,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            provider="orion-local",
            model_version="hybrid-runtime-v1",
            metadata={"fingerprint": fingerprint},
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("hybrid runtime clock must be timezone-aware")
        return value


def _serialize_model(model: ContractModel) -> bytes:
    payload = model.model_dump(mode="json")
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _hybrid_output_dimensions(
    width: int,
    height: int,
    *,
    resolution: str,
) -> tuple[int, int]:
    base = 1_080 if resolution == "1080p" else 720
    ratio = width / height
    if ratio < 0.8:
        return base, base * 16 // 9
    if ratio > 1.25:
        return base * 16 // 9, base
    return base, base
