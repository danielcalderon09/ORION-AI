"""Sequential durable provider-neutral speech-generation handler."""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
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
    SpeechReplacementLineageError,
    SpeechSourceScriptError,
)
from backend.src.production.speech_generation.fitting_recovery import (
    FilesystemNarrationFittingRecoveryAuthorizationStore,
    NarrationFittingRecoveryAuthorization,
    NarrationFittingRecoveryAuthorizationError,
)
from backend.src.production.speech_generation.local_narration_fitter import (
    DeterministicSpanishNarrationFitter,
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
from backend.src.production.speech_generation.narration_fitting import (
    DisabledNarrationFittingProvider,
    LocalNarrationFitter,
    NarrationFittingConfiguration,
    NarrationFittingProvider,
    NarrationFittingProviderError,
    NarrationFittingRecord,
    NarrationFittingRequest,
    NarrationFittingStatus,
    NarrationFittingStrategy,
    NarrationFittingUncertainError,
    local_narration_fitting_fingerprint,
    narration_fitting_fingerprint,
    narration_text_hash,
    validate_narration_revision,
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
        narration_fitter: NarrationFittingProvider | None = None,
        local_narration_fitter: LocalNarrationFitter | None = None,
        narration_fitting_configuration: NarrationFittingConfiguration | None = None,
        fitting_recovery_store: (
            FilesystemNarrationFittingRecoveryAuthorizationStore | None
        ) = None,
    ) -> None:
        self._reader = script_reader
        self._provider = provider
        self._store = audio_store
        self._writer = manifest_writer
        self._configuration = configuration
        self._clock = clock
        self._duration_resolution_policy = duration_resolution_policy or DurationResolutionPolicy()
        self._narration_fitter = narration_fitter or DisabledNarrationFittingProvider()
        self._local_narration_fitter = (
            local_narration_fitter or DeterministicSpanishNarrationFitter()
        )
        self._fitting_configuration = (
            narration_fitting_configuration or NarrationFittingConfiguration()
        )
        self._fitting_recovery_store = fitting_recovery_store

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
            recovery_authorization = None
            recovery_store = self._fitting_recovery_store
            if (
                existing is None
                and command.attempt_number > 1
                and recovery_store is not None
            ):
                recovery_authorization = await recovery_store.read(
                    job_id=command.job_id,
                    target_attempt_number=command.attempt_number,
                )
            if recovery_authorization is not None:
                assert recovery_store is not None
                source_manifest = await recovery_store.load_source_manifest(
                    recovery_authorization
                )
                manifest = self._recovery_manifest(
                    command=command,
                    source=source,
                    segments=segments,
                    source_manifest=source_manifest,
                    authorization=recovery_authorization,
                )
            else:
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
            for source_segment in segments:
                entry = _entry_for_source(manifest, source_segment.segment_id)
                segment = _active_segment(source_segment, entry)
                request = self._write_request(command, context, segment)
                if entry.status is SpeechSegmentStatus.STORED:
                    asset = await self._recover_stored(entry, request)
                    stored_assets[entry.segment_id] = asset
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
                    stored_assets[entry.segment_id] = recovered
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
                    stored_assets[stored.segment_id] = verified.asset
                except asyncio.CancelledError:
                    raise
                except SpeechReplacementLineageError:
                    await self._checkpoint_failed(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                        error_code="speech_replacement_lineage_blocked",
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.NEEDS_USER_ACTION,
                        "speech_replacement_lineage_blocked",
                    )
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

            manifest, stored_assets, fitting_error = await self._fit_until_accepted(
                command=command,
                context=context,
                source=source,
                source_segments=segments,
                manifest=manifest,
                stored_assets=stored_assets,
                recovery_authorization=recovery_authorization,
            )
            if fitting_error is not None:
                outcome = (
                    StageOutcome.NEEDS_USER_ACTION
                    if fitting_error
                    in {
                        "narration_fitting_uncertain",
                        "speech_replacement_lineage_blocked",
                    }
                    else StageOutcome.FAILED_PERMANENT
                )
                return self._failure(command, started_at, outcome, fitting_error)
            durable_resolution = manifest.duration_resolution
            assert durable_resolution is not None and durable_resolution.accepted
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
        except (
            SpeechManifestError,
            SpeechGenerationError,
            NarrationFittingRecoveryAuthorizationError,
            ValueError,
        ):
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "speech_generation_invalid",
            )

    async def _fit_until_accepted(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        source: ReadSpeechSourceScript,
        source_segments: tuple[SpeechSegmentRequest, ...],
        manifest: SpeechGenerationManifest,
        stored_assets: dict[str, SpeechBinaryAsset],
        recovery_authorization: NarrationFittingRecoveryAuthorization | None,
    ) -> tuple[
        SpeechGenerationManifest,
        dict[str, SpeechBinaryAsset],
        str | None,
    ]:
        if (
            manifest.status is SpeechGenerationManifestStatus.COMPLETED
            and manifest.duration_resolution is not None
            and manifest.duration_resolution.accepted
        ):
            return manifest, stored_assets, None
        while True:
            scene_ids = tuple(entry.source_scene_id for entry in manifest.entries)
            planned = tuple(entry.target_duration_ms or 0 for entry in manifest.entries)
            narration = tuple(entry.duration_ms or 0 for entry in manifest.entries)
            try:
                resolution = resolve_audio_first_durations(
                    requested_target_duration_ms=sum(planned),
                    planned_scene_durations_ms=planned,
                    narration_scene_durations_ms=narration,
                    policy=self._duration_resolution_policy,
                )
            except DurationResolutionError as exc:
                durable = durable_duration_resolution(
                    scene_ids=scene_ids,
                    planned_scene_durations_ms=planned,
                    narration_scene_durations_ms=narration,
                    resolution=exc.resolution,
                )
                rejected = manifest.model_copy(
                    update={
                        "status": SpeechGenerationManifestStatus.FAILED,
                        "duration_resolution": durable,
                        "updated_at": self._aware_now(),
                    }
                )
                if rejected != manifest:
                    await self._writer.checkpoint(
                        context=context,
                        previous=manifest,
                        current=rejected,
                    )
                manifest = rejected
            else:
                durable = durable_duration_resolution(
                    scene_ids=scene_ids,
                    planned_scene_durations_ms=planned,
                    narration_scene_durations_ms=narration,
                    resolution=resolution,
                )
                accepted = manifest.model_copy(
                    update={
                        "status": SpeechGenerationManifestStatus.IN_PROGRESS,
                        "duration_resolution": durable,
                        "updated_at": self._aware_now(),
                    }
                )
                if accepted != manifest:
                    await self._writer.checkpoint(
                        context=context,
                        previous=manifest,
                        current=accepted,
                    )
                return accepted, stored_assets, None

            attempt = 1 + max(
                (
                    record.attempt_number
                    for record in manifest.fitting_records
                    if record.strategy is NarrationFittingStrategy.REMOTE_PROVIDER
                ),
                default=0,
            )
            if attempt > self._fitting_configuration.maximum_attempts:
                return manifest, stored_assets, "narration_fitting_exhausted"
            overrun_candidates = tuple(
                entry
                for entry in manifest.entries
                if entry.duration_ms is not None
                and entry.target_duration_ms is not None
                and entry.duration_ms > entry.target_duration_ms
            )
            if not overrun_candidates:
                return manifest, stored_assets, "narration_fitting_exhausted"
            candidates = overrun_candidates
            local_round_exists = any(
                record.attempt_number == attempt
                and record.strategy is NarrationFittingStrategy.DETERMINISTIC_LOCAL
                for record in manifest.fitting_records
            )
            if attempt > 1 or local_round_exists:
                assert manifest.duration_resolution is not None
                excess = (
                    manifest.duration_resolution.resolved_duration_ms
                    - manifest.duration_resolution.maximum_allowed_duration_ms
                )
                selected: list[SpeechSegmentManifestEntry] = []
                recoverable = 0
                for item in sorted(
                    overrun_candidates,
                    key=lambda value: (
                        -((value.duration_ms or 0) - (value.target_duration_ms or 0)),
                        value.sequence_index,
                    ),
                ):
                    selected.append(item)
                    recoverable += (item.duration_ms or 0) - (item.target_duration_ms or 0)
                    if recoverable >= excess:
                        break
                candidates = tuple(sorted(selected, key=lambda value: value.sequence_index))
            locally_revised: set[str] = set()
            for candidate in candidates:
                manifest, record = await self._completed_local_fitting_record(
                    command=command,
                    context=context,
                    source=source,
                    manifest=manifest,
                    entry=candidate,
                    attempt=attempt,
                )
                if record is None:
                    continue
                current_entry = next(
                    item
                    for item in manifest.entries
                    if item.source_scene_id == candidate.source_scene_id
                )
                if current_entry.normalized_text_hash == record.previous_text_hash:
                    old_segment_id = current_entry.segment_id
                    manifest = await self._apply_fitting_record(
                        context=context,
                        manifest=manifest,
                        entry=current_entry,
                        record=record,
                    )
                    stored_assets.pop(old_segment_id, None)
                    locally_revised.add(candidate.source_scene_id)
            if locally_revised:
                for scene_id in sorted(locally_revised):
                    current_entry = next(
                        item for item in manifest.entries if item.source_scene_id == scene_id
                    )
                    source_segment = next(
                        item for item in source_segments if item.scene_id == scene_id
                    )
                    active_segment = _active_segment(source_segment, current_entry)
                    manifest, asset, error = await self._generate_fitted_audio(
                        command=command,
                        context=context,
                        manifest=manifest,
                        segment=active_segment,
                    )
                    if error is not None:
                        return manifest, stored_assets, error
                    assert asset is not None
                    stored_assets[active_segment.segment_id] = asset
                continue

            if self._fitting_configuration.provider == "disabled":
                return manifest, stored_assets, "duration_resolution_invalid"
            remotely_revised: set[str] = set()
            for candidate in candidates:
                manifest, record, error = await self._completed_fitting_record(
                    command=command,
                    context=context,
                    source=source,
                    manifest=manifest,
                    entry=candidate,
                    attempt=attempt,
                    recovery_authorization=recovery_authorization,
                )
                if error is not None:
                    return manifest, stored_assets, error
                assert record.revised_narration is not None
                assert record.revised_text_hash is not None
                current_entry = next(
                    item
                    for item in manifest.entries
                    if item.source_scene_id == candidate.source_scene_id
                )
                if current_entry.normalized_text_hash == record.previous_text_hash:
                    old_segment_id = current_entry.segment_id
                    manifest = await self._apply_fitting_record(
                        context=context,
                        manifest=manifest,
                        entry=current_entry,
                        record=record,
                    )
                    stored_assets.pop(old_segment_id, None)
                    remotely_revised.add(candidate.source_scene_id)
            for scene_id in sorted(remotely_revised):
                current_entry = next(
                    item for item in manifest.entries if item.source_scene_id == scene_id
                )
                source_segment = next(
                    item for item in source_segments if item.scene_id == scene_id
                )
                active_segment = _active_segment(source_segment, current_entry)
                if current_entry.status is not SpeechSegmentStatus.STORED:
                    manifest, asset, error = await self._generate_fitted_audio(
                        command=command,
                        context=context,
                        manifest=manifest,
                        segment=active_segment,
                    )
                    if error is not None:
                        return manifest, stored_assets, error
                    assert asset is not None
                    stored_assets[active_segment.segment_id] = asset

    async def _completed_local_fitting_record(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        source: ReadSpeechSourceScript,
        manifest: SpeechGenerationManifest,
        entry: SpeechSegmentManifestEntry,
        attempt: int,
    ) -> tuple[SpeechGenerationManifest, NarrationFittingRecord | None]:
        existing = next(
            (
                record
                for record in manifest.fitting_records
                if record.scene_id == entry.source_scene_id
                and record.attempt_number == attempt
                and record.strategy is NarrationFittingStrategy.DETERMINISTIC_LOCAL
            ),
            None,
        )
        if existing is not None:
            return manifest, existing
        request = self._fitting_request(
            command,
            source,
            entry,
            attempt,
            maximum_provider_retries=0,
        )
        result = self._local_narration_fitter.revise(request)
        if result is None:
            return manifest, None
        revised = validate_narration_revision(entry.narration_text, result.revised_narration)
        assert entry.duration_ms is not None and entry.target_duration_ms is not None
        assert entry.audio_binary_asset_id is not None
        assert entry.storage_path is not None
        assert entry.sha256 is not None
        overrun = entry.duration_ms - entry.target_duration_ms
        now = self._aware_now()
        record = NarrationFittingRecord(
            scene_id=entry.source_scene_id,
            sequence_index=entry.sequence_index,
            attempt_number=attempt,
            strategy=NarrationFittingStrategy.DETERMINISTIC_LOCAL,
            rules_applied=result.rules_applied,
            previous_text_hash=entry.normalized_text_hash,
            revised_text_hash=narration_text_hash(revised),
            revised_narration=revised,
            previous_duration_ms=entry.duration_ms,
            previous_audio_binary_asset_id=entry.audio_binary_asset_id,
            previous_audio_storage_path=entry.storage_path,
            previous_audio_sha256=entry.sha256,
            target_duration_ms=entry.target_duration_ms,
            overrun_ms=overrun,
            overrun_ratio=Decimal(overrun) / Decimal(entry.target_duration_ms),
            provider=self._local_narration_fitter.name,
            model=self._local_narration_fitter.model,
            estimated_cost_usd=Decimal(0),
            maximum_authorized_cost_usd=Decimal(0),
            request_fingerprint=local_narration_fitting_fingerprint(
                request,
                revised_narration=revised,
                rules_applied=result.rules_applied,
                model=self._local_narration_fitter.model,
            ),
            status=NarrationFittingStatus.COMPLETED,
            fresh_submission_permitted=False,
            prepared_at=now,
            terminal_at=now,
            retryable=False,
        )
        current = manifest.model_copy(
            update={
                "fitting_records": (*manifest.fitting_records, record),
                "status": SpeechGenerationManifestStatus.IN_PROGRESS,
                "updated_at": now,
            }
        )
        await self._writer.checkpoint(context=context, previous=manifest, current=current)
        return current, record

    async def _apply_fitting_record(
        self,
        *,
        context: StageContext,
        manifest: SpeechGenerationManifest,
        entry: SpeechSegmentManifestEntry,
        record: NarrationFittingRecord,
    ) -> SpeechGenerationManifest:
        assert record.revised_narration is not None
        assert record.revised_text_hash is not None
        revision = entry.fitting_revision + 1
        revised_entry = entry.model_copy(
            update={
                "segment_id": _revised_segment_id(
                    entry.source_segment_id or entry.segment_id,
                    revision,
                    record.revised_text_hash,
                ),
                "source_segment_id": entry.source_segment_id or entry.segment_id,
                "fitting_revision": revision,
                "narration_text": record.revised_narration,
                "normalized_text_hash": record.revised_text_hash,
                "status": SpeechSegmentStatus.PENDING,
                "audio_binary_asset_id": None,
                "audio_artifact_id": None,
                "storage_path": None,
                "mime_type": None,
                "extension": None,
                "sha256": None,
                "size_bytes": None,
                "duration_ms": None,
                "sample_rate_hz": None,
                "channel_count": None,
                "sample_width_bytes": None,
                "frame_count": None,
                "provider": None,
                "generation_started_at": None,
                "created_at": None,
                "error_code": None,
                "metadata": {
                    "fitting_attempt": record.attempt_number,
                    "fitting_strategy": record.strategy.value,
                    "previous_duration_ms": record.previous_duration_ms,
                },
            }
        )
        revised = replace_speech_entry(
            manifest,
            revised_entry,
            status=SpeechGenerationManifestStatus.IN_PROGRESS,
            updated_at=self._aware_now(),
        )
        await self._writer.checkpoint(context=context, previous=manifest, current=revised)
        return revised

    async def _completed_fitting_record(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        source: ReadSpeechSourceScript,
        manifest: SpeechGenerationManifest,
        entry: SpeechSegmentManifestEntry,
        attempt: int,
        recovery_authorization: NarrationFittingRecoveryAuthorization | None,
    ) -> tuple[SpeechGenerationManifest, NarrationFittingRecord, str | None]:
        existing = next(
            (
                record
                for record in manifest.fitting_records
                if record.scene_id == entry.source_scene_id
                and record.attempt_number == attempt
                and record.strategy is NarrationFittingStrategy.REMOTE_PROVIDER
            ),
            None,
        )
        if existing is not None:
            if existing.status is NarrationFittingStatus.COMPLETED:
                return manifest, existing, None
            if existing.status in {
                NarrationFittingStatus.SUBMITTING,
                NarrationFittingStatus.UNCERTAIN,
            }:
                if existing.status is NarrationFittingStatus.SUBMITTING:
                    uncertain = existing.model_copy(
                        update={
                            "status": NarrationFittingStatus.UNCERTAIN,
                            "terminal_at": self._aware_now(),
                            "safe_error_code": "interrupted_submission",
                        }
                    )
                    manifest = await self._replace_fitting_record(
                        context=context,
                        manifest=manifest,
                        record=uncertain,
                    )
                    existing = uncertain
                return manifest, existing, "narration_fitting_uncertain"
            return manifest, existing, "narration_fitting_provider_error"
        configuration = self._fitting_configuration
        assert configuration.estimated_cost_usd_per_attempt is not None
        assert configuration.maximum_estimated_cost_usd_per_attempt is not None
        assert configuration.maximum_estimated_job_cost_usd is not None
        job_budget = self._effective_fitting_job_budget(recovery_authorization)
        projected = (
            sum(
                (
                    record.estimated_cost_usd * (1 + record.provider_retry_count)
                    for record in manifest.fitting_records
                ),
                Decimal(0),
            )
            + configuration.estimated_cost_usd_per_attempt
        )
        if projected > job_budget:
            placeholder = self._prepared_fitting_record(
                command=command,
                source=source,
                entry=entry,
                attempt=attempt,
                maximum_provider_retries=0,
            )
            return manifest, placeholder, "narration_fitting_cost_policy"
        maximum_provider_retries = self._maximum_provider_retries(
            manifest,
            job_budget=job_budget,
        )
        request = self._fitting_request(
            command,
            source,
            entry,
            attempt,
            maximum_provider_retries=maximum_provider_retries,
        )
        prepared = self._prepared_fitting_record(
            command=command,
            source=source,
            entry=entry,
            attempt=attempt,
            maximum_provider_retries=maximum_provider_retries,
        )
        prepared_manifest = manifest.model_copy(
            update={
                "fitting_records": (*manifest.fitting_records, prepared),
                "status": SpeechGenerationManifestStatus.IN_PROGRESS,
                "updated_at": self._aware_now(),
            }
        )
        await self._writer.checkpoint(
            context=context,
            previous=manifest,
            current=prepared_manifest,
        )
        submitting = prepared.model_copy(
            update={
                "status": NarrationFittingStatus.SUBMITTING,
                "fresh_submission_permitted": False,
                "submission_started_at": self._aware_now(),
            }
        )
        manifest = await self._replace_fitting_record(
            context=context,
            manifest=prepared_manifest,
            record=submitting,
        )
        logger.info(
            "fitting narration text",
            extra={
                "job_id": str(command.job_id),
                "scene_id": entry.source_scene_id,
                "fitting_attempt": attempt,
            },
        )
        try:
            result = await self._narration_fitter.revise(request)
        except asyncio.CancelledError:
            raise
        except NarrationFittingUncertainError:
            uncertain = submitting.model_copy(
                update={
                    "status": NarrationFittingStatus.UNCERTAIN,
                    "terminal_at": self._aware_now(),
                    "safe_error_code": "uncertain_transport",
                }
            )
            manifest = await self._replace_fitting_record(
                context=context,
                manifest=manifest,
                record=uncertain,
            )
            return manifest, uncertain, "narration_fitting_uncertain"
        except NarrationFittingProviderError as exc:
            failed = submitting.model_copy(
                update={
                    "status": NarrationFittingStatus.FAILED,
                    "terminal_at": self._aware_now(),
                    "safe_error_code": exc.safe_error_code,
                    "retryable": exc.retryable,
                    "http_status": exc.http_status,
                    "provider_request_id": exc.provider_request_id,
                    "response_headers_received": exc.response_headers_received,
                    "response_received": exc.response_received,
                    "provider_retry_count": exc.provider_retry_count,
                }
            )
            manifest = await self._replace_fitting_record(
                context=context,
                manifest=manifest,
                record=failed,
            )
            return manifest, failed, "narration_fitting_provider_error"
        try:
            revised_narration = validate_narration_revision(
                entry.narration_text,
                result.revised_narration,
            )
        except NarrationFittingProviderError:
            failed = submitting.model_copy(
                update={
                    "status": NarrationFittingStatus.FAILED,
                    "terminal_at": self._aware_now(),
                    "safe_error_code": "revision_contract",
                    "retryable": False,
                    "response_received": result.response_received,
                    "response_headers_received": result.response_headers_received,
                    "provider_retry_count": result.provider_retry_count,
                }
            )
            manifest = await self._replace_fitting_record(
                context=context,
                manifest=manifest,
                record=failed,
            )
            return manifest, failed, "narration_fitting_provider_error"
        completed = submitting.model_copy(
            update={
                "status": NarrationFittingStatus.COMPLETED,
                "terminal_at": self._aware_now(),
                "revised_narration": revised_narration,
                "revised_text_hash": narration_text_hash(revised_narration),
                "http_status": result.http_status,
                "provider_request_id": result.provider_request_id,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.total_tokens,
                "reported_cost_usd": result.reported_cost_usd,
                "finish_reason": result.finish_reason,
                "response_headers_received": result.response_headers_received,
                "response_received": result.response_received,
                "provider_retry_count": result.provider_retry_count,
                "retryable": False,
            }
        )
        manifest = await self._replace_fitting_record(
            context=context,
            manifest=manifest,
            record=completed,
        )
        return manifest, completed, None

    def _fitting_request(
        self,
        command: StageCommand,
        source: ReadSpeechSourceScript,
        entry: SpeechSegmentManifestEntry,
        attempt: int,
        *,
        maximum_provider_retries: int,
    ) -> NarrationFittingRequest:
        assert entry.duration_ms is not None and entry.target_duration_ms is not None
        return NarrationFittingRequest(
            job_id=command.job_id,
            scene_id=entry.source_scene_id,
            sequence_index=entry.sequence_index,
            attempt_number=attempt,
            current_narration=entry.narration_text,
            current_duration_ms=entry.duration_ms,
            target_duration_ms=entry.target_duration_ms,
            language=source.script.language,
            tone=source.script.tone,
            maximum_provider_retries=maximum_provider_retries,
        )

    def _prepared_fitting_record(
        self,
        *,
        command: StageCommand,
        source: ReadSpeechSourceScript,
        entry: SpeechSegmentManifestEntry,
        attempt: int,
        maximum_provider_retries: int,
    ) -> NarrationFittingRecord:
        request = self._fitting_request(
            command,
            source,
            entry,
            attempt,
            maximum_provider_retries=maximum_provider_retries,
        )
        configuration = self._fitting_configuration
        assert configuration.estimated_cost_usd_per_attempt is not None
        assert configuration.maximum_estimated_cost_usd_per_attempt is not None
        assert entry.duration_ms is not None and entry.target_duration_ms is not None
        assert entry.audio_binary_asset_id is not None
        assert entry.storage_path is not None
        assert entry.sha256 is not None
        overrun = entry.duration_ms - entry.target_duration_ms
        return NarrationFittingRecord(
            scene_id=entry.source_scene_id,
            sequence_index=entry.sequence_index,
            attempt_number=attempt,
            strategy=NarrationFittingStrategy.REMOTE_PROVIDER,
            previous_text_hash=entry.normalized_text_hash,
            previous_duration_ms=entry.duration_ms,
            previous_audio_binary_asset_id=entry.audio_binary_asset_id,
            previous_audio_storage_path=entry.storage_path,
            previous_audio_sha256=entry.sha256,
            target_duration_ms=entry.target_duration_ms,
            overrun_ms=overrun,
            overrun_ratio=(Decimal(overrun) / Decimal(entry.target_duration_ms)),
            provider=self._narration_fitter.name,
            model=self._narration_fitter.model,
            estimated_cost_usd=configuration.estimated_cost_usd_per_attempt,
            maximum_authorized_cost_usd=(configuration.maximum_estimated_cost_usd_per_attempt),
            request_fingerprint=narration_fitting_fingerprint(
                request,
                self._narration_fitter.model,
            ),
            status=NarrationFittingStatus.PREPARED,
            fresh_submission_permitted=True,
            prepared_at=self._aware_now(),
        )

    def _maximum_provider_retries(
        self,
        manifest: SpeechGenerationManifest,
        *,
        job_budget: Decimal,
    ) -> int:
        configuration = self._fitting_configuration
        assert configuration.estimated_cost_usd_per_attempt is not None
        spent = sum(
            (
                record.estimated_cost_usd * (1 + record.provider_retry_count)
                for record in manifest.fitting_records
            ),
            Decimal(0),
        )
        available = (
            job_budget
            - spent
            - configuration.estimated_cost_usd_per_attempt
        )
        retries = configuration.maximum_provider_retries
        while retries > 0 and available < (
            configuration.estimated_cost_usd_per_attempt * retries
        ):
            retries -= 1
        return retries

    def _effective_fitting_job_budget(
        self,
        authorization: NarrationFittingRecoveryAuthorization | None,
    ) -> Decimal:
        configuration = self._fitting_configuration
        assert configuration.maximum_estimated_job_cost_usd is not None
        if authorization is None:
            return configuration.maximum_estimated_job_cost_usd
        if (
            authorization.new_authorized_job_cost_usd
            > configuration.maximum_estimated_job_cost_usd
        ):
            raise NarrationFittingRecoveryAuthorizationError(
                "recovery authorization exceeds active Settings"
            )
        return authorization.new_authorized_job_cost_usd

    async def _replace_fitting_record(
        self,
        *,
        context: StageContext,
        manifest: SpeechGenerationManifest,
        record: NarrationFittingRecord,
    ) -> SpeechGenerationManifest:
        records = tuple(
            record
            if item.scene_id == record.scene_id
            and item.attempt_number == record.attempt_number
            and item.strategy is record.strategy
            else item
            for item in manifest.fitting_records
        )
        current = manifest.model_copy(
            update={
                "fitting_records": records,
                "updated_at": self._aware_now(),
            }
        )
        await self._writer.checkpoint(
            context=context,
            previous=manifest,
            current=current,
        )
        return current

    async def _generate_fitted_audio(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        manifest: SpeechGenerationManifest,
        segment: SpeechSegmentRequest,
    ) -> tuple[SpeechGenerationManifest, SpeechBinaryAsset | None, str | None]:
        entry = _entry_for(manifest, segment.segment_id)
        request = self._write_request(command, context, segment)
        recovered = await self._store.recover(request=request)
        if recovered is not None:
            stored = self._stored_entry(entry=entry, asset=recovered, recovered=True)
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
            return current, recovered, None
        generating = entry.model_copy(
            update={
                "status": SpeechSegmentStatus.GENERATING,
                "generation_attempt_count": entry.generation_attempt_count + 1,
                "generation_started_at": self._aware_now(),
                "error_code": None,
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
        logger.info(
            "fitting narration audio",
            extra={
                "job_id": str(command.job_id),
                "scene_id": entry.source_scene_id,
                "fitting_attempt": entry.fitting_revision,
            },
        )
        try:
            response = await self._provider.generate(
                self._provider_request(command, context, segment)
            )
            self._validate_response(response, request.expected)
            if self._configuration.provider == "openrouter":
                request = request.model_copy(update={"expected": response.audio})
            asset = await self._store.write(request=request, content=response.content)
            verified = await self._store.read(asset=asset)
        except asyncio.CancelledError:
            raise
        except SpeechReplacementLineageError:
            await self._checkpoint_failed(
                context=context,
                manifest=manifest,
                entry=generating,
                error_code="speech_replacement_lineage_blocked",
            )
            return manifest, None, "speech_replacement_lineage_blocked"
        except SpeechProviderUncertainError:
            await self._checkpoint_uncertain(
                context=context,
                manifest=manifest,
                entry=generating,
            )
            return manifest, None, "speech_submission_uncertain"
        except (SpeechProviderError, SpeechAudioStoreError):
            await self._checkpoint_failed(
                context=context,
                manifest=manifest,
                entry=generating,
            )
            return manifest, None, "speech_segment_generation_failed"
        stored = self._stored_entry(entry=generating, asset=verified.asset, recovered=False)
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
        return current, verified.asset, None

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
                source_segment_id=segment.segment_id,
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
            or tuple(entry.source_segment_id or entry.segment_id for entry in manifest.entries)
            != tuple(segment.segment_id for segment in segments)
        ):
            raise SpeechManifestError("speech manifest source or configuration changed")

    def _recovery_manifest(
        self,
        *,
        command: StageCommand,
        source: ReadSpeechSourceScript,
        segments: tuple[SpeechSegmentRequest, ...],
        source_manifest: SpeechGenerationManifest,
        authorization: NarrationFittingRecoveryAuthorization,
    ) -> SpeechGenerationManifest:
        if (
            authorization.job_id != command.job_id
            or authorization.source_attempt_number != source_manifest.attempt_number
            or command.attempt_number != source_manifest.attempt_number + 1
        ):
            raise NarrationFittingRecoveryAuthorizationError(
                "recovery authorization does not match the next stage attempt"
            )
        candidate = source_manifest.model_copy(
            update={
                "attempt_number": command.attempt_number,
                "status": SpeechGenerationManifestStatus.IN_PROGRESS,
                "updated_at": self._aware_now(),
                "metadata": {
                    **source_manifest.metadata,
                    "recovered_from_attempt": source_manifest.attempt_number,
                    "fitting_recovery_record_sha256": authorization.fingerprint,
                },
            }
        )
        self._validate_existing(candidate, source, segments, command)
        if not any(
            record.status is NarrationFittingStatus.FAILED
            for record in candidate.fitting_records
        ):
            raise NarrationFittingRecoveryAuthorizationError(
                "source manifest has no failed fitting to recover"
            )
        return SpeechGenerationManifest.model_validate(candidate.model_dump(mode="python"))

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
        error_code: str = "speech_generation_failed",
    ) -> None:
        failed = entry.model_copy(
            update={
                "status": SpeechSegmentStatus.FAILED,
                "error_code": error_code,
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


def _entry_for_source(
    manifest: SpeechGenerationManifest,
    source_segment_id: str,
) -> SpeechSegmentManifestEntry:
    return next(
        entry
        for entry in manifest.entries
        if (entry.source_segment_id or entry.segment_id) == source_segment_id
    )


def _active_segment(
    source: SpeechSegmentRequest,
    entry: SpeechSegmentManifestEntry,
) -> SpeechSegmentRequest:
    if (
        source.segment_id == entry.segment_id
        and source.normalized_text_hash == entry.normalized_text_hash
    ):
        return source
    return source.model_copy(
        update={
            "segment_id": entry.segment_id,
            "narration_text": entry.narration_text,
            "normalized_text_hash": entry.normalized_text_hash,
        }
    )


def _revised_segment_id(source_segment_id: str, attempt: int, revised_hash: str) -> str:
    digest = hashlib.sha256(f"{source_segment_id}:{attempt}:{revised_hash}".encode()).hexdigest()[
        :32
    ]
    return f"segment-{digest}"


def _audio_artifact_id(job_id: UUID, segment_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"orion:{job_id}:speech-audio:{segment_id}")


def _manifest_artifact_id(job_id: UUID, attempt_number: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orion:{job_id}:speech-generation-manifest:{attempt_number}",
    )
