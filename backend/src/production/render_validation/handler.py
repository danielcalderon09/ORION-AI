"""Durable FINAL acceptance handler for VALIDATING_RENDER."""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionStage,
)
from backend.src.production.render_validation.exceptions import (
    FinalRenderConflictError,
    FinalRenderCorruptError,
    FinalRenderSourceError,
    FinalRenderStorageError,
    FinalRenderValidationError,
)
from backend.src.production.render_validation.models import (
    FinalRenderValidationManifest,
    FinalValidationStatus,
)
from backend.src.production.render_validation.ports import (
    FinalRenderProbe,
    FinalRenderSourceReader,
    FinalRenderValidationStore,
    FinalValidationClock,
    VerifiedFinalRenderSource,
)
from backend.src.production.render_validation.recovery import (
    failed_manifest,
    prepared_manifest,
    source_failure_manifest,
    validated_manifest,
    validating_manifest,
)
from backend.src.production.rendering.exceptions import RenderingValidationError
from backend.src.production.rendering.output_probe import ProbedRenderOutput
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput


class FinalRenderValidationHandler:
    supported_stages = frozenset({ProductionStage.VALIDATING_RENDER})

    def __init__(
        self,
        *,
        source_reader: FinalRenderSourceReader,
        store: FinalRenderValidationStore,
        probe: FinalRenderProbe,
        clock: FinalValidationClock,
    ) -> None:
        self._source_reader = source_reader
        self._store = store
        self._probe = probe
        self._clock = clock

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput:
        started_at = self._aware_now()
        self._validate_context(command, context)
        manifest: FinalRenderValidationManifest | None = None
        try:
            manifest = await self._store.read_manifest(context=context)
            if manifest is not None and manifest.status is FinalValidationStatus.VALIDATED:
                return await self._validated_replay(
                    command=command,
                    context=context,
                    manifest=manifest,
                    started_at=started_at,
                )
            if manifest is not None and manifest.status is FinalValidationStatus.FAILED:
                return await self._terminal_failure(
                    command=command,
                    context=context,
                    manifest=manifest,
                    started_at=started_at,
                    code=manifest.error_codes[0],
                )
            source = await self._source_reader.read(
                context=context,
                input_artifact_ids=command.input_artifact_ids,
            )
            if manifest is None:
                manifest = prepared_manifest(
                    source=source,
                    attempt_number=context.attempt_number,
                    now=self._aware_now(),
                )
                await self._store.create_manifest(context=context, manifest=manifest)
            else:
                _validate_manifest_source(manifest, source)
            if manifest.status is FinalValidationStatus.PREPARED:
                checkpoint = validating_manifest(manifest, now=self._aware_now())
                await self._store.checkpoint_manifest(
                    context=context,
                    previous=manifest,
                    current=checkpoint,
                )
                manifest = checkpoint
            if manifest.status is not FinalValidationStatus.VALIDATING:
                raise FinalRenderValidationError(
                    "validation_state_invalid",
                    "final-render validation state cannot resume",
                )
            inspected = await self._probe.probe(source)
            _validate_probe_against_source(inspected, source)
            checkpoint = validated_manifest(
                manifest,
                probe=inspected,
                now=self._aware_now(),
            )
            await self._store.checkpoint_manifest(
                context=context,
                previous=manifest,
                current=checkpoint,
            )
            return await self._success(
                command=command,
                context=context,
                manifest=checkpoint,
                started_at=started_at,
                probe_executed=True,
            )
        except asyncio.CancelledError:
            raise
        except FinalRenderSourceError as exc:
            failure = await self._durable_failure(
                context=context,
                manifest=manifest,
                code=exc.code,
            )
            return await self._terminal_failure(
                command=command,
                context=context,
                manifest=failure,
                started_at=started_at,
                code=exc.code,
            )
        except RenderingValidationError as exc:
            code = getattr(exc, "code", "ffprobe_validation_failed")
            failure = await self._durable_failure(
                context=context,
                manifest=manifest,
                code=code,
            )
            return await self._terminal_failure(
                command=command,
                context=context,
                manifest=failure,
                started_at=started_at,
                code=code,
            )
        except FinalRenderConflictError as exc:
            return self._failure_result(
                command,
                started_at,
                StageOutcome.FAILED_TRANSIENT,
                exc.code,
                retry_after_seconds=1,
            )
        except (FinalRenderCorruptError, FinalRenderStorageError) as exc:
            return self._failure_result(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                exc.code,
            )
        except FinalRenderValidationError as exc:
            failure = await self._durable_failure(
                context=context,
                manifest=manifest,
                code=exc.code,
            )
            return await self._terminal_failure(
                command=command,
                context=context,
                manifest=failure,
                started_at=started_at,
                code=exc.code,
            )
        except ValueError:
            return self._failure_result(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "final_render_validation_invalid",
            )

    async def _validated_replay(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        manifest: FinalRenderValidationManifest,
        started_at: datetime,
    ) -> StageExecutionOutput:
        if (
            manifest.render_relative_path is None
            or manifest.render_size_bytes is None
            or manifest.render_checksum is None
        ):
            raise FinalRenderCorruptError(
                "validated_render_identity_missing",
                "validated final-render identity is incomplete",
            )
        actual = await self._store.media_identity(relative_path=manifest.render_relative_path)
        if actual != (manifest.render_size_bytes, manifest.render_checksum):
            return self._failure_result(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "validated_render_changed",
            )
        return await self._success(
            command=command,
            context=context,
            manifest=manifest,
            started_at=started_at,
            probe_executed=False,
        )

    async def _durable_failure(
        self,
        *,
        context: StageContext,
        manifest: FinalRenderValidationManifest | None,
        code: str,
    ) -> FinalRenderValidationManifest:
        if manifest is None:
            failure = source_failure_manifest(
                job_id=context.job_id,
                attempt_number=context.attempt_number,
                code=code,
                now=self._aware_now(),
            )
            await self._store.create_manifest(context=context, manifest=failure)
            return failure
        failure = failed_manifest(manifest, code=code, now=self._aware_now())
        await self._store.checkpoint_manifest(
            context=context,
            previous=manifest,
            current=failure,
        )
        return failure

    async def _success(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        manifest: FinalRenderValidationManifest,
        started_at: datetime,
        probe_executed: bool,
    ) -> StageExecutionOutput:
        artifact = await self._manifest_artifact(command, context, manifest)
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.SUCCEEDED,
                started_at=started_at,
                finished_at=self._aware_now(),
                progress_percent=100,
                output_artifact_ids=(artifact.artifact_id,),
                metadata={
                    "final_render_accepted": True,
                    "ffmpeg_executed": False,
                    "ffprobe_revalidated": probe_executed,
                    "plan_fingerprint": manifest.plan_fingerprint,
                    "validation_fingerprint": manifest.validation_fingerprint,
                },
            ),
            artifacts=(artifact,),
        )

    async def _terminal_failure(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        manifest: FinalRenderValidationManifest,
        started_at: datetime,
        code: str,
    ) -> StageExecutionOutput:
        artifact = await self._manifest_artifact(command, context, manifest)
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.FAILED_PERMANENT,
                started_at=started_at,
                finished_at=self._aware_now(),
                progress_percent=0,
                output_artifact_ids=(artifact.artifact_id,),
                error_code=code,
                error_message="Final render validation failed",
                metadata={
                    "final_render_accepted": False,
                    "ffmpeg_executed": False,
                    "render_preserved": True,
                    "validation_fingerprint": manifest.validation_fingerprint,
                },
            ),
            artifacts=(artifact,),
        )

    async def _manifest_artifact(
        self,
        command: StageCommand,
        context: StageContext,
        manifest: FinalRenderValidationManifest,
    ) -> Artifact:
        relative, size, digest = await self._store.manifest_identity(context=context)
        return Artifact(
            artifact_id=uuid5(
                NAMESPACE_URL,
                f"orion:final-render-validation:{command.job_id}:{context.attempt_number}",
            ),
            job_id=command.job_id,
            artifact_type=ArtifactType.FINAL_RENDER_VALIDATION,
            relative_path=relative,
            mime_type="application/json",
            status=ArtifactStatus.READY,
            size_bytes=size,
            sha256=digest,
            provider="orion-final-render-validation",
            model_version=manifest.schema_version,
            metadata={
                "render_artifact_id": (
                    str(manifest.render_artifact_id)
                    if manifest.render_artifact_id is not None
                    else None
                ),
                "validation_fingerprint": manifest.validation_fingerprint,
                "validation_result": manifest.validation_result.value,
            },
        )

    @staticmethod
    def _validate_context(command: StageCommand, context: StageContext) -> None:
        if command.stage is not ProductionStage.VALIDATING_RENDER or context.stage != (
            command.stage
        ):
            raise ValueError("final-render validation received another stage")
        if command.job_id != context.job_id or command.command_id != context.command_id:
            raise ValueError("final-render validation context differs from command")

    def _failure_result(
        self,
        command: StageCommand,
        started_at: datetime,
        outcome: StageOutcome,
        code: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> StageExecutionOutput:
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=outcome,
                started_at=started_at,
                finished_at=self._aware_now(),
                progress_percent=0,
                error_code=code,
                error_message="Final render validation failed",
                retry_after_seconds=retry_after_seconds,
                metadata={
                    "final_render_accepted": False,
                    "ffmpeg_executed": False,
                    "render_preserved": True,
                },
            )
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("final-render validation clock must be timezone-aware")
        return value


def _validate_manifest_source(
    manifest: FinalRenderValidationManifest,
    source: VerifiedFinalRenderSource,
) -> None:
    expected = (
        source.render_artifact.artifact_id,
        source.render_artifact.relative_path,
        source.render_artifact.sha256,
        source.render_artifact.size_bytes,
        source.render_manifest_artifact.artifact_id,
        source.composition_plan_artifact.artifact_id,
        source.execution_plan_artifact.artifact_id,
        source.request.request_fingerprint,
        source.composition_plan.plan_fingerprint,
        source.composition_plan.timeline_checksum,
        source.execution_plan.argument_fingerprint,
    )
    actual = (
        manifest.render_artifact_id,
        manifest.render_relative_path,
        manifest.render_checksum,
        manifest.render_size_bytes,
        manifest.render_manifest_artifact_id,
        manifest.media_composition_plan_artifact_id,
        manifest.execution_plan_artifact_id,
        manifest.fingerprints.request_fingerprint,
        manifest.plan_fingerprint,
        manifest.fingerprints.timeline_checksum,
        manifest.execution_plan_fingerprint,
    )
    if actual != expected:
        raise FinalRenderSourceError(
            "validation_source_changed",
            "final-render validation source changed during recovery",
        )


def _validate_probe_against_source(
    probe: ProbedRenderOutput,
    source: VerifiedFinalRenderSource,
) -> None:
    request = source.request
    result = source.render_manifest.ffmpeg_result
    expected_subtitles = 1 if source.execution_plan.subtitle_strategy == "mux_mov_text" else 0
    actual_rate = probe.frame_rate_numerator / probe.frame_rate_denominator
    expected_rate = request.frame_rate_numerator / request.frame_rate_denominator
    if (
        result is None
        or result.request_fingerprint != request.request_fingerprint
        or probe.video_stream_count != 1
        or probe.audio_stream_count < 1
        or probe.subtitle_stream_count != expected_subtitles
        or probe.width != request.output_width
        or probe.height != request.output_height
        or abs(actual_rate - expected_rate)
        > source.execution_plan.execution_policy.frame_rate_tolerance
        or abs(probe.duration_ms - request.expected_duration_ms)
        > source.execution_plan.execution_policy.duration_tolerance_ms
        or probe.video_codec not in {"h264", "avc1"}
        or probe.audio_codec != "aac"
        or probe.pixel_format != "yuv420p"
        or not {"mov", "mp4"}.intersection(probe.format_names)
        or (
            probe.duration_ms,
            probe.duration_frames,
            probe.width,
            probe.height,
            probe.frame_rate_numerator,
            probe.frame_rate_denominator,
            probe.video_codec,
            probe.audio_codec,
            probe.pixel_format,
            probe.video_stream_count,
            probe.audio_stream_count,
            probe.subtitle_stream_count,
            probe.probe_fingerprint,
        )
        != (
            result.duration_ms,
            result.duration_frames,
            result.width,
            result.height,
            result.frame_rate_numerator,
            result.frame_rate_denominator,
            result.video_codec,
            result.audio_codec,
            result.pixel_format,
            result.video_stream_count,
            result.audio_stream_count,
            result.subtitle_stream_count,
            result.probe_fingerprint,
        )
    ):
        raise FinalRenderValidationError(
            "final_probe_mismatch",
            "independent FFprobe result differs from durable render expectations",
        )
