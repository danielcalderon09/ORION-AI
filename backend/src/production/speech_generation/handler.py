"""Sequential durable provider-neutral speech-generation handler."""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.duration_resolution import (
    DurationResolutionError,
    DurationResolutionPolicy,
    durable_duration_resolution,
    resolve_audio_first_durations,
)
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionStage,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput
from backend.src.production.speech_generation.configuration import (
    SpeechGenerationConfiguration,
)
from backend.src.production.speech_generation.duration import simulated_duration_ms
from backend.src.production.speech_generation.exceptions import (
    SpeechAudioStoreError,
    SpeechGenerationError,
    SpeechManifestConflictError,
    SpeechManifestError,
    SpeechProviderError,
    SpeechProviderResponseError,
    SpeechProviderUncertainError,
    SpeechSourceScriptError,
)
from backend.src.production.speech_generation.manifest_writer import (
    speech_manifest_relative_path,
)
from backend.src.production.speech_generation.models import (
    SpeechAudioWriteRequest,
    SpeechBinaryAsset,
    SpeechBinaryAssetMetadata,
    SpeechGenerationManifest,
    SpeechGenerationManifestStatus,
    SpeechSegmentAudioMetadata,
    SpeechSegmentManifestEntry,
    SpeechSegmentRequest,
    SpeechSegmentStatus,
    replace_speech_entry,
    summarize_speech_entries,
)
from backend.src.production.speech_generation.ports import (
    ReadSpeechSourceScript,
    SpeechAudioStore,
    SpeechGenerationProvider,
    SpeechManifestWriter,
    SpeechProviderRequest,
    SpeechProviderResult,
    SpeechSourceScriptReader,
)
from backend.src.production.speech_generation.segment_builder import (
    build_speech_segments,
)
from backend.src.production.speech_generation.serialization import (
    serialize_speech_manifest,
)

logger = logging.getLogger(__name__)


class SpeechGenerationHandler:
    supported_stages = frozenset({ProductionStage.GENERATING_NARRATION})

    def __init__(
        self,
        *,
        script_reader: SpeechSourceScriptReader,
        provider: SpeechGenerationProvider,
        audio_store: SpeechAudioStore,
        manifest_writer: SpeechManifestWriter,
        configuration: SpeechGenerationConfiguration,
        clock: Callable[[], datetime],
        duration_resolution_policy: DurationResolutionPolicy | None = None,
    ) -> None:
        self._reader = script_reader
        self._provider = provider
        self._store = audio_store
        self._writer = manifest_writer
        self._configuration = configuration
        self._clock = clock
        self._duration_resolution_policy = (
            duration_resolution_policy or DurationResolutionPolicy()
        )

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput:
        if command.stage is not ProductionStage.GENERATING_NARRATION:
            raise ValueError("handler supports only generating_narration")
        if context.command_id != command.command_id:
            raise ValueError("StageContext does not belong to StageCommand")
        started_at = self._aware_now()
        try:
            source = await self._reader.read_for_speech_generation(context=context)
            segments = build_speech_segments(source, self._configuration)
            existing = await self._writer.read_existing(context=context)
            manifest = existing or self._initial_manifest(
                command=command,
                source=source,
                segments=segments,
            )
            if existing is None:
                await self._writer.create(context=context, manifest=manifest)
            else:
                self._validate_existing(manifest, source, segments, command)

            stored_assets: dict[str, SpeechBinaryAsset] = {}
            for segment in segments:
                entry = _entry_for(manifest, segment.segment_id)
                request = self._write_request(command, context, segment)
                if entry.status is SpeechSegmentStatus.STORED:
                    asset = await self._recover_stored(entry, request)
                    stored_assets[segment.segment_id] = asset
                    continue

                recovered = await self._store.recover(request=request)
                if recovered is not None:
                    if entry.status in {
                        SpeechSegmentStatus.PENDING,
                        SpeechSegmentStatus.FAILED,
                    }:
                        recovering = entry.model_copy(
                            update={
                                "status": SpeechSegmentStatus.GENERATING,
                                "error_code": None,
                                "generation_started_at": self._aware_now(),
                            }
                        )
                        current = replace_speech_entry(
                            manifest,
                            recovering,
                            status=SpeechGenerationManifestStatus.IN_PROGRESS,
                            updated_at=self._aware_now(),
                        )
                        await self._writer.checkpoint(
                            context=context,
                            previous=manifest,
                            current=current,
                        )
                        manifest = current
                        entry = recovering
                    stored = self._stored_entry(
                        entry=entry,
                        asset=recovered,
                        recovered=True,
                    )
                    current = replace_speech_entry(
                        manifest,
                        stored,
                        status=SpeechGenerationManifestStatus.IN_PROGRESS,
                        updated_at=self._aware_now(),
                    )
                    await self._writer.checkpoint(
                        context=context,
                        previous=manifest,
                        current=current,
                    )
                    manifest = current
                    stored_assets[segment.segment_id] = recovered
                    continue

                if entry.status is SpeechSegmentStatus.GENERATING:
                    assert entry.generation_started_at is not None
                    age = (self._aware_now() - entry.generation_started_at).total_seconds()
                    if age < self._configuration.generating_stale_after_seconds:
                        return self._failure(
                            command,
                            started_at,
                            StageOutcome.FAILED_TRANSIENT,
                            "speech_generation_in_progress",
                            retry_after_seconds=(
                                self._configuration.generating_stale_after_seconds - age
                            ),
                        )
                    if self._configuration.provider == "openrouter":
                        uncertain = entry.model_copy(
                            update={
                                "status": SpeechSegmentStatus.UNCERTAIN,
                                "error_code": "remote_submission_uncertain",
                                "generation_started_at": None,
                            }
                        )
                        current = replace_speech_entry(
                            manifest,
                            uncertain,
                            status=SpeechGenerationManifestStatus.UNCERTAIN,
                            updated_at=self._aware_now(),
                        )
                        await self._writer.checkpoint(
                            context=context, previous=manifest, current=current
                        )
                        return self._failure(
                            command,
                            started_at,
                            StageOutcome.NEEDS_USER_ACTION,
                            "speech_submission_uncertain",
                        )
                    interrupted = entry.model_copy(
                        update={
                            "status": SpeechSegmentStatus.FAILED,
                            "error_code": "generation_interrupted",
                            "generation_started_at": None,
                        }
                    )
                    current = replace_speech_entry(
                        manifest,
                        interrupted,
                        status=(
                            SpeechGenerationManifestStatus.PARTIAL
                            if manifest.summary.stored
                            else SpeechGenerationManifestStatus.FAILED
                        ),
                        updated_at=self._aware_now(),
                    )
                    await self._writer.checkpoint(
                        context=context,
                        previous=manifest,
                        current=current,
                    )
                    manifest = current
                    entry = interrupted
                elif entry.status is SpeechSegmentStatus.UNCERTAIN:
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.NEEDS_USER_ACTION,
                        "speech_submission_uncertain",
                    )

                generating = entry.model_copy(
                    update={
                        "status": SpeechSegmentStatus.GENERATING,
                        "error_code": None,
                        "generation_attempt_count": entry.generation_attempt_count + 1,
                        "generation_started_at": self._aware_now(),
                    }
                )
                current = replace_speech_entry(
                    manifest,
                    generating,
                    status=SpeechGenerationManifestStatus.IN_PROGRESS,
                    updated_at=self._aware_now(),
                )
                await self._writer.checkpoint(
                    context=context,
                    previous=manifest,
                    current=current,
                )
                manifest = current
                try:
                    response = await self._provider.generate(
                        self._provider_request(command, context, segment)
                    )
                    self._validate_response(response, request.expected)
                    if self._configuration.provider == "openrouter":
                        request = request.model_copy(update={"expected": response.audio})
                    asset = await self._store.write(
                        request=request,
                        content=response.content,
                    )
                    verified = await self._store.read(asset=asset)
                    stored = self._stored_entry(
                        entry=generating,
                        asset=verified.asset,
                        recovered=False,
                    )
                    current = replace_speech_entry(
                        manifest,
                        stored,
                        status=SpeechGenerationManifestStatus.IN_PROGRESS,
                        updated_at=self._aware_now(),
                    )
                    await self._writer.checkpoint(
                        context=context,
                        previous=manifest,
                        current=current,
                    )
                    manifest = current
                    stored_assets[segment.segment_id] = verified.asset
                except asyncio.CancelledError:
                    raise
                except SpeechProviderUncertainError:
                    await self._checkpoint_uncertain(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.NEEDS_USER_ACTION,
                        "speech_submission_uncertain",
                    )
                except (SpeechProviderError, SpeechAudioStoreError):
                    await self._checkpoint_failed(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.FAILED_PERMANENT,
                        "speech_segment_generation_failed",
                    )

            scene_ids = tuple(entry.source_scene_id for entry in manifest.entries)
            planned_durations = tuple(
                entry.target_duration_ms or 0 for entry in manifest.entries
            )
            narration_durations = tuple(
                entry.duration_ms or 0 for entry in manifest.entries
            )
            try:
                resolution = resolve_audio_first_durations(
                    requested_target_duration_ms=sum(planned_durations),
                    planned_scene_durations_ms=planned_durations,
                    narration_scene_durations_ms=narration_durations,
                    policy=self._duration_resolution_policy,
                )
            except DurationResolutionError as exc:
                rejected = durable_duration_resolution(
                    scene_ids=scene_ids,
                    planned_scene_durations_ms=planned_durations,
                    narration_scene_durations_ms=narration_durations,
                    resolution=exc.resolution,
                )
                failed = manifest.model_copy(
                    update={
                        "status": SpeechGenerationManifestStatus.FAILED,
                        "duration_resolution": rejected,
                        "updated_at": self._aware_now(),
                    }
                )
                await self._writer.checkpoint(
                    context=context,
                    previous=manifest,
                    current=failed,
                )
                return self._failure(
                    command,
                    started_at,
                    StageOutcome.FAILED_PERMANENT,
                    "duration_resolution_invalid",
                )
            durable_resolution = durable_duration_resolution(
                scene_ids=scene_ids,
                planned_scene_durations_ms=planned_durations,
                narration_scene_durations_ms=narration_durations,
                resolution=resolution,
            )
            completed = manifest.model_copy(
                update={
                    "status": SpeechGenerationManifestStatus.COMPLETED,
                    "duration_resolution": durable_resolution,
                    "updated_at": self._aware_now(),
                }
            )
            await self._writer.finalize(
                context=context,
                previous=manifest,
                current=completed,
            )
            return self._success(
                command=command,
                context=context,
                source=source,
                manifest=completed,
                assets=stored_assets,
                started_at=started_at,
            )
        except asyncio.CancelledError:
            raise
        except SpeechManifestConflictError:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_TRANSIENT,
                "speech_checkpoint_conflict",
                retry_after_seconds=1,
            )
        except SpeechSourceScriptError:
            return self._failure(
                command,
                started_at,
                StageOutcome.NEEDS_USER_ACTION,
                "source_script_invalid",
            )
        except (SpeechManifestError, SpeechGenerationError, ValueError):
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "speech_generation_invalid",
            )

    def _initial_manifest(
        self,
        *,
        command: StageCommand,
        source: ReadSpeechSourceScript,
        segments: tuple[SpeechSegmentRequest, ...],
    ) -> SpeechGenerationManifest:
        entries = tuple(
            SpeechSegmentManifestEntry(
                segment_id=segment.segment_id,
                sequence_index=segment.sequence_index,
                source_scene_id=segment.scene_id,
                source_shot_id=segment.shot_id,
                narration_text=segment.narration_text,
                normalized_text_hash=segment.normalized_text_hash,
                target_duration_ms=segment.target_duration_ms,
                timing_provenance=segment.timing_provenance,
                status=SpeechSegmentStatus.PENDING,
            )
            for segment in segments
        )
        now = self._aware_now()
        return SpeechGenerationManifest(
            job_id=command.job_id,
            attempt_number=command.attempt_number,
            source_script_schema_version=source.schema_version,
            source_script_artifact_id=source.artifact_id,
            source_script_sha256=source.sha256,
            provider=self._configuration.provider,
            requested_voice=self._configuration.voice,
            requested_language=segments[0].requested_language,
            requested_speaking_rate=self._configuration.words_per_minute,
            configuration_fingerprint=self._configuration.fingerprint(),
            entries=entries,
            summary=summarize_speech_entries(entries),
            status=SpeechGenerationManifestStatus.IN_PROGRESS,
            created_at=now,
            updated_at=now,
            metadata={
                "checkpointed": True,
                "deterministic": self._configuration.provider == "simulated",
                "network": self._configuration.provider == "openrouter",
                "sequential": True,
                "simulated": self._configuration.provider == "simulated",
            },
        )

    def _validate_existing(
        self,
        manifest: SpeechGenerationManifest,
        source: ReadSpeechSourceScript,
        segments: tuple[SpeechSegmentRequest, ...],
        command: StageCommand,
    ) -> None:
        if (
            manifest.job_id != command.job_id
            or manifest.attempt_number != command.attempt_number
            or manifest.source_script_artifact_id != source.artifact_id
            or manifest.source_script_sha256 != source.sha256
            or manifest.source_script_schema_version != source.schema_version
            or manifest.configuration_fingerprint != self._configuration.fingerprint()
            or tuple(entry.segment_id for entry in manifest.entries)
            != tuple(segment.segment_id for segment in segments)
            or tuple(entry.normalized_text_hash for entry in manifest.entries)
            != tuple(segment.normalized_text_hash for segment in segments)
        ):
            raise SpeechManifestError("speech manifest source or configuration changed")

    def _write_request(
        self,
        command: StageCommand,
        context: StageContext,
        segment: SpeechSegmentRequest,
    ) -> SpeechAudioWriteRequest:
        provider_request = self._provider_request(command, context, segment)
        duration_ms = simulated_duration_ms(provider_request)
        frames = max(
            1,
            round(duration_ms * self._configuration.sample_rate_hz / 1_000),
        )
        return SpeechAudioWriteRequest(
            job_id=command.job_id,
            segment=segment,
            expected=SpeechSegmentAudioMetadata(
                duration_ms=round(frames * 1_000 / self._configuration.sample_rate_hz),
                sample_rate_hz=self._configuration.sample_rate_hz,
                channel_count=self._configuration.channel_count,
                sample_width_bytes=self._configuration.sample_width_bytes,
                frame_count=frames,
            ),
            metadata=SpeechBinaryAssetMetadata(
                source_script_artifact_id=segment.source_script_artifact_id,
                source_script_sha256=segment.source_script_sha256,
                normalized_text_hash=segment.normalized_text_hash,
                configuration_fingerprint=self._configuration.fingerprint(),
                provider=self._provider.name,
                requested_voice=segment.requested_voice,
                requested_language=segment.requested_language,
                deterministic=self._configuration.provider == "simulated",
                attributes={
                    "simulated": self._configuration.provider == "simulated",
                    "network": self._configuration.provider == "openrouter",
                    "timing_provenance": segment.timing_provenance.value,
                },
            ),
            flexible_duration=self._configuration.provider == "openrouter",
        )

    def _provider_request(
        self,
        command: StageCommand,
        context: StageContext,
        segment: SpeechSegmentRequest,
    ) -> SpeechProviderRequest:
        return SpeechProviderRequest(
            job_id=command.job_id,
            command_id=command.command_id,
            correlation_id=context.correlation_id,
            attempt_number=command.attempt_number,
            segment=segment,
            configuration=self._configuration,
            fingerprint=self._configuration.fingerprint(),
        )

    @staticmethod
    def _validate_response(
        response: SpeechProviderResult,
        expected: SpeechSegmentAudioMetadata,
    ) -> None:
        if response.mime_type != "audio/wav" or (
            response.audio != expected and response.provider != "openrouter"
        ):
            raise SpeechProviderResponseError("speech provider result differs from request")
        if response.provider == "openrouter" and (
            response.audio.sample_rate_hz != expected.sample_rate_hz
            or response.audio.channel_count != expected.channel_count
            or response.audio.sample_width_bytes != expected.sample_width_bytes
        ):
            raise SpeechProviderResponseError("speech provider format differs from request")

    async def _recover_stored(
        self,
        entry: SpeechSegmentManifestEntry,
        request: SpeechAudioWriteRequest,
    ) -> SpeechBinaryAsset:
        resolved = await self._store.resolve(
            job_id=request.job_id,
            segment_id=request.segment.segment_id,
        )
        asset = resolved.asset
        if (
            entry.audio_binary_asset_id != asset.asset_id
            or entry.storage_path != asset.storage_path
            or entry.sha256 != asset.sha256
            or entry.size_bytes != asset.size_bytes
            or entry.duration_ms != asset.duration_ms
            or entry.sample_rate_hz != asset.sample_rate_hz
            or entry.channel_count != asset.channel_count
            or entry.sample_width_bytes != asset.sample_width_bytes
            or entry.frame_count != asset.frame_count
            or asset.metadata.source_script_sha256 != request.segment.source_script_sha256
            or asset.metadata.normalized_text_hash != request.segment.normalized_text_hash
            or asset.metadata.configuration_fingerprint != self._configuration.fingerprint()
        ):
            raise SpeechAudioStoreError("stored speech asset provenance changed")
        return asset

    @staticmethod
    def _stored_entry(
        *,
        entry: SpeechSegmentManifestEntry,
        asset: SpeechBinaryAsset,
        recovered: bool,
    ) -> SpeechSegmentManifestEntry:
        return entry.model_copy(
            update={
                "status": SpeechSegmentStatus.STORED,
                "audio_binary_asset_id": asset.asset_id,
                "audio_artifact_id": _audio_artifact_id(
                    asset.job_id,
                    entry.segment_id,
                ),
                "storage_path": asset.storage_path,
                "mime_type": asset.mime_type,
                "extension": asset.extension,
                "sha256": asset.sha256,
                "size_bytes": asset.size_bytes,
                "duration_ms": asset.duration_ms,
                "sample_rate_hz": asset.sample_rate_hz,
                "channel_count": asset.channel_count,
                "sample_width_bytes": asset.sample_width_bytes,
                "frame_count": asset.frame_count,
                "provider": asset.metadata.provider,
                "generation_started_at": None,
                "created_at": asset.created_at,
                "error_code": None,
                "metadata": {
                    "deterministic": asset.metadata.deterministic,
                    "recovered": recovered,
                    "simulated": asset.metadata.attributes.get("simulated", False),
                },
            }
        )

    async def _checkpoint_uncertain(
        self,
        *,
        context: StageContext,
        manifest: SpeechGenerationManifest,
        entry: SpeechSegmentManifestEntry,
    ) -> None:
        uncertain = entry.model_copy(
            update={
                "status": SpeechSegmentStatus.UNCERTAIN,
                "error_code": "remote_submission_uncertain",
                "generation_started_at": None,
            }
        )
        current = replace_speech_entry(
            manifest,
            uncertain,
            status=SpeechGenerationManifestStatus.UNCERTAIN,
            updated_at=self._aware_now(),
        )
        await self._writer.checkpoint(
            context=context,
            previous=manifest,
            current=current,
        )

    async def _checkpoint_failed(
        self,
        *,
        context: StageContext,
        manifest: SpeechGenerationManifest,
        entry: SpeechSegmentManifestEntry,
    ) -> None:
        failed = entry.model_copy(
            update={
                "status": SpeechSegmentStatus.FAILED,
                "error_code": "speech_generation_failed",
                "generation_started_at": None,
            }
        )
        current = replace_speech_entry(
            manifest,
            failed,
            status=(
                SpeechGenerationManifestStatus.PARTIAL
                if manifest.summary.stored
                else SpeechGenerationManifestStatus.FAILED
            ),
            updated_at=self._aware_now(),
        )
        await self._writer.checkpoint(
            context=context,
            previous=manifest,
            current=current,
        )

    def _success(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        source: ReadSpeechSourceScript,
        manifest: SpeechGenerationManifest,
        assets: dict[str, SpeechBinaryAsset],
        started_at: datetime,
    ) -> StageExecutionOutput:
        artifacts = [
            Artifact(
                artifact_id=_audio_artifact_id(command.job_id, entry.segment_id),
                job_id=command.job_id,
                artifact_type=ArtifactType.NARRATION,
                relative_path=assets[entry.segment_id].storage_path,
                mime_type="audio/wav",
                status=ArtifactStatus.READY,
                size_bytes=entry.size_bytes,
                sha256=entry.sha256,
                duration_seconds=(entry.duration_ms or 0) / 1_000,
                provider=entry.provider,
                model_version=self._configuration.voice,
                metadata={
                    "segment_id": entry.segment_id,
                    "sequence_index": entry.sequence_index,
                    "source_scene_id": entry.source_scene_id,
                    "source_shot_id": entry.source_shot_id,
                    "source_script_artifact_id": str(source.artifact_id),
                    "source_script_sha256": source.sha256,
                    "normalized_text_hash": entry.normalized_text_hash,
                    "duration_ms": entry.duration_ms,
                    "sample_rate_hz": entry.sample_rate_hz,
                    "channel_count": entry.channel_count,
                    "sample_width_bytes": entry.sample_width_bytes,
                    "simulated": True,
                    "deterministic": True,
                },
            )
            for entry in manifest.entries
        ]
        content = serialize_speech_manifest(manifest)
        artifacts.append(
            Artifact(
                artifact_id=_manifest_artifact_id(
                    command.job_id,
                    command.attempt_number,
                ),
                job_id=command.job_id,
                artifact_type=ArtifactType.PRODUCTION_SPEECH_GENERATION_MANIFEST,
                relative_path=speech_manifest_relative_path(context),
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                provider=self._configuration.provider,
                model_version=self._configuration.voice,
                metadata={
                    "schema_version": manifest.schema_version,
                    "segment_count": manifest.summary.total,
                    "stored_count": manifest.summary.stored,
                    "total_duration_ms": manifest.summary.total_duration_ms,
                    "requested_target_duration_ms": (
                        manifest.duration_resolution.requested_target_duration_ms
                        if manifest.duration_resolution is not None
                        else None
                    ),
                    "resolved_duration_ms": (
                        manifest.duration_resolution.resolved_duration_ms
                        if manifest.duration_resolution is not None
                        else None
                    ),
                    "maximum_allowed_duration_ms": (
                        manifest.duration_resolution.maximum_allowed_duration_ms
                        if manifest.duration_resolution is not None
                        else None
                    ),
                    "language": manifest.requested_language,
                    "voice": manifest.requested_voice,
                    "sample_rate_hz": self._configuration.sample_rate_hz,
                    "status": manifest.status.value,
                    "simulated": True,
                    "checkpointed": True,
                },
            )
        )
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=StageOutcome.SUCCEEDED,
                started_at=started_at,
                finished_at=self._aware_now(),
                progress_percent=100,
                output_artifact_ids=tuple(item.artifact_id for item in artifacts),
                metadata={
                    "handler": type(self).__name__,
                    "provider": self._configuration.provider,
                    "segment_count": manifest.summary.total,
                    "total_duration_ms": manifest.summary.total_duration_ms,
                    "resolved_duration_ms": (
                        manifest.duration_resolution.resolved_duration_ms
                        if manifest.duration_resolution is not None
                        else None
                    ),
                    "language": manifest.requested_language,
                    "voice": manifest.requested_voice,
                    "sample_rate_hz": self._configuration.sample_rate_hz,
                    "simulated": True,
                    "checkpointed": True,
                },
            ),
            artifacts=tuple(artifacts),
        )

    def _failure(
        self,
        command: StageCommand,
        started_at: datetime,
        outcome: StageOutcome,
        error_code: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> StageExecutionOutput:
        logger.warning(
            "speech generation stage did not complete",
            extra={
                "job_id": str(command.job_id),
                "command_id": str(command.command_id),
                "attempt": command.attempt_number,
                "outcome": outcome.value,
                "error_code": error_code,
            },
        )
        return StageExecutionOutput(
            result=StageResult(
                command_id=command.command_id,
                job_id=command.job_id,
                stage=command.stage,
                outcome=outcome,
                started_at=started_at,
                finished_at=self._aware_now(),
                progress_percent=0,
                error_code=error_code,
                error_message="Speech generation stage could not complete",
                retry_after_seconds=retry_after_seconds,
                metadata={
                    "handler": type(self).__name__,
                    "error_category": error_code,
                    "simulated": True,
                },
            )
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("speech generation clock must be timezone-aware")
        return value


def _entry_for(
    manifest: SpeechGenerationManifest,
    segment_id: str,
) -> SpeechSegmentManifestEntry:
    return next(entry for entry in manifest.entries if entry.segment_id == segment_id)


def _audio_artifact_id(job_id: UUID, segment_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"orion:{job_id}:speech-audio:{segment_id}")


def _manifest_artifact_id(job_id: UUID, attempt_number: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orion:{job_id}:speech-generation-manifest:{attempt_number}",
    )
