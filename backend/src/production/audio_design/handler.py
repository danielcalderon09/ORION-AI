"""Sequential durable handler for the existing PREPARING_MUSIC stage."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome, StageResult
from backend.src.production.audio_design.configuration import (
    AudioDesignConfiguration,
)
from backend.src.production.audio_design.duration import (
    duration_for_frame_count,
    frame_count_for_duration,
)
from backend.src.production.audio_design.exceptions import (
    AudioDesignError,
    AudioDesignManifestConflictError,
    AudioDesignManifestError,
    AudioDesignProviderError,
    AudioDesignProviderResponseError,
    AudioDesignSourceError,
    AudioDesignStoreError,
    AudioDesignStoreNotFoundError,
)
from backend.src.production.audio_design.fingerprints import (
    music_request_fingerprint,
    sound_effect_request_fingerprint,
)
from backend.src.production.audio_design.manifest_store import (
    audio_design_manifest_relative_path,
)
from backend.src.production.audio_design.models import (
    AudioAssetExpectation,
    AudioAssetKind,
    AudioDesignAssetStatus,
    AudioDesignManifest,
    AudioDesignManifestEntry,
    AudioDesignManifestStatus,
    AudioDesignPlan,
    AudioFormatExpectation,
    AudioPcmMetadata,
    GeneratedAudioResult,
    MusicGenerationRequest,
    SoundEffectGenerationRequest,
    StoredAudioDesignAsset,
    replace_audio_design_entry,
    summarize_audio_design_entries,
)
from backend.src.production.audio_design.plan import derive_audio_design_plan
from backend.src.production.audio_design.ports import (
    AudioDesignAssetStore,
    AudioDesignManifestStore,
    AudioDesignSourceScriptReader,
    MusicGenerationProvider,
    ReadAudioDesignSourceScript,
    SoundEffectGenerationProvider,
)
from backend.src.production.audio_design.serialization import (
    serialize_audio_design_manifest,
)
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import (
    ArtifactStatus,
    ArtifactType,
    ProductionStage,
)
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.runtime_models import StageExecutionOutput


class AudioDesignHandler:
    """Generate and checkpoint one deterministic local WAV at a time."""

    supported_stages = frozenset({ProductionStage.PREPARING_MUSIC})

    def __init__(
        self,
        *,
        script_reader: AudioDesignSourceScriptReader,
        music_provider: MusicGenerationProvider,
        sound_effect_provider: SoundEffectGenerationProvider,
        music_store: AudioDesignAssetStore,
        sound_effect_store: AudioDesignAssetStore,
        manifest_store: AudioDesignManifestStore,
        configuration: AudioDesignConfiguration,
        clock: Callable[[], datetime],
    ) -> None:
        self._reader = script_reader
        self._music_provider = music_provider
        self._sound_effect_provider = sound_effect_provider
        self._music_store = music_store
        self._sound_effect_store = sound_effect_store
        self._manifest_store = manifest_store
        self._configuration = configuration
        self._clock = clock

    async def execute(
        self,
        command: StageCommand,
        context: StageContext,
    ) -> StageExecutionOutput:
        if command.stage is not ProductionStage.PREPARING_MUSIC:
            raise ValueError("handler supports only preparing_music")
        if context.command_id != command.command_id:
            raise ValueError("StageContext does not belong to StageCommand")
        started_at = self._aware_now()
        try:
            source = await self._reader.read_for_audio_design(context=context)
            plan = derive_audio_design_plan(
                job_id=command.job_id,
                source_script_artifact_id=source.artifact_id,
                source_script_sha256=source.sha256,
                script=source.script,
                configuration=self._configuration,
            )
            requests = self._requests(plan)
            existing = await self._manifest_store.read_existing(context=context)
            manifest = existing or self._initial_manifest(
                command=command,
                source=source,
                plan=plan,
                requests=requests,
            )
            if existing is None:
                await self._manifest_store.create(context=context, manifest=manifest)
            else:
                self._validate_existing(
                    manifest=manifest,
                    source=source,
                    plan=plan,
                    command=command,
                    requests=requests,
                )

            assets: dict[str, StoredAudioDesignAsset] = {}
            for request in requests:
                entry = _entry_for(manifest, request.requirement_id)
                expectation = self._expectation(
                    command.job_id,
                    request,
                    entry.provider_id,
                )
                store = self._store_for(entry.kind)
                if entry.status is AudioDesignAssetStatus.STORED:
                    try:
                        asset = await self._recover_stored(entry, expectation, store)
                        assets[entry.requirement_id] = asset
                        continue
                    except AudioDesignStoreNotFoundError:
                        missing = entry.model_copy(
                            update={
                                "status": AudioDesignAssetStatus.FAILED,
                                "stored_at": None,
                                "asset_id": None,
                                "artifact_id": None,
                                "storage_path": None,
                                "sha256": None,
                                "size_bytes": None,
                                "error_code": "stored_audio_missing",
                                "metadata": {"recovery_required": True},
                            }
                        )
                        current = replace_audio_design_entry(
                            manifest,
                            missing,
                            status=AudioDesignManifestStatus.FAILED,
                            updated_at=self._aware_now(),
                        )
                        await self._manifest_store.checkpoint(
                            context=context,
                            previous=manifest,
                            current=current,
                        )
                        manifest = current
                        entry = missing

                recovered = await store.recover(expectation=expectation)
                if recovered is not None:
                    if entry.status is not AudioDesignAssetStatus.GENERATING:
                        manifest, entry = await self._checkpoint_generating(
                            context=context,
                            manifest=manifest,
                            entry=entry,
                            increment=False,
                        )
                    stored_entry = self._stored_entry(
                        entry=entry,
                        asset=recovered,
                        recovered=True,
                    )
                    current = replace_audio_design_entry(
                        manifest,
                        stored_entry,
                        status=AudioDesignManifestStatus.GENERATING,
                        updated_at=self._aware_now(),
                    )
                    await self._manifest_store.checkpoint(
                        context=context,
                        previous=manifest,
                        current=current,
                    )
                    manifest = current
                    assets[entry.requirement_id] = recovered
                    continue

                if entry.status is AudioDesignAssetStatus.GENERATING:
                    assert entry.generation_started_at is not None
                    age = (self._aware_now() - entry.generation_started_at).total_seconds()
                    if age < self._configuration.generating_stale_after_seconds:
                        return self._failure(
                            command,
                            started_at,
                            StageOutcome.FAILED_TRANSIENT,
                            "audio_design_generation_in_progress",
                            retry_after_seconds=(
                                self._configuration.generating_stale_after_seconds - age
                            ),
                        )
                    failed = entry.model_copy(
                        update={
                            "status": AudioDesignAssetStatus.FAILED,
                            "generation_started_at": None,
                            "error_code": "generation_interrupted",
                        }
                    )
                    current = replace_audio_design_entry(
                        manifest,
                        failed,
                        status=AudioDesignManifestStatus.FAILED,
                        updated_at=self._aware_now(),
                    )
                    await self._manifest_store.checkpoint(
                        context=context,
                        previous=manifest,
                        current=current,
                    )
                    manifest = current
                    entry = failed

                manifest, generating = await self._checkpoint_generating(
                    context=context,
                    manifest=manifest,
                    entry=entry,
                    increment=True,
                )
                try:
                    result = await self._generate(request)
                    self._validate_result(result, expectation.audio, generating.provider_id)
                    asset = await store.write(
                        expectation=expectation,
                        content=result.content,
                    )
                    verified = await store.read(asset=asset)
                    stored_entry = self._stored_entry(
                        entry=generating,
                        asset=verified.asset,
                        recovered=False,
                    )
                    current = replace_audio_design_entry(
                        manifest,
                        stored_entry,
                        status=AudioDesignManifestStatus.GENERATING,
                        updated_at=self._aware_now(),
                    )
                    await self._manifest_store.checkpoint(
                        context=context,
                        previous=manifest,
                        current=current,
                    )
                    manifest = current
                    assets[entry.requirement_id] = verified.asset
                except asyncio.CancelledError:
                    raise
                except (AudioDesignProviderError, AudioDesignStoreError):
                    await self._checkpoint_failed(
                        context=context,
                        manifest=manifest,
                        entry=generating,
                    )
                    return self._failure(
                        command,
                        started_at,
                        StageOutcome.FAILED_PERMANENT,
                        "audio_design_asset_generation_failed",
                    )

            if manifest.status is not AudioDesignManifestStatus.COMPLETE:
                completed = manifest.model_copy(
                    update={
                        "status": AudioDesignManifestStatus.COMPLETE,
                        "updated_at": self._aware_now(),
                    }
                )
                await self._manifest_store.finalize(
                    context=context,
                    previous=manifest,
                    current=completed,
                )
                manifest = completed
            return self._success(
                command=command,
                context=context,
                source=source,
                plan=plan,
                manifest=manifest,
                assets=assets,
                started_at=started_at,
            )
        except asyncio.CancelledError:
            raise
        except AudioDesignManifestConflictError:
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_TRANSIENT,
                "audio_design_checkpoint_conflict",
                retry_after_seconds=1,
            )
        except AudioDesignSourceError:
            return self._failure(
                command,
                started_at,
                StageOutcome.NEEDS_USER_ACTION,
                "audio_design_source_script_invalid",
            )
        except (AudioDesignManifestError, AudioDesignError, ValueError):
            return self._failure(
                command,
                started_at,
                StageOutcome.FAILED_PERMANENT,
                "audio_design_invalid",
            )

    def _requests(
        self,
        plan: AudioDesignPlan,
    ) -> tuple[MusicGenerationRequest | SoundEffectGenerationRequest, ...]:
        requests: list[MusicGenerationRequest | SoundEffectGenerationRequest] = []
        if plan.music_requirement is not None:
            requirement = plan.music_requirement
            fingerprint = music_request_fingerprint(
                requirement,
                sample_rate_hz=self._configuration.sample_rate_hz,
                channel_count=self._configuration.channel_count,
                sample_width_bytes=self._configuration.sample_width_bytes,
            )
            requests.append(
                MusicGenerationRequest(
                    request_id=f"music-request-{fingerprint[:24]}",
                    requirement_id=requirement.requirement_id,
                    mood=requirement.mood,
                    intensity=requirement.intensity,
                    duration_ms=requirement.target_duration_ms,
                    loopable=requirement.loopable,
                    request_fingerprint=fingerprint,
                )
            )
        for sound_effect_requirement in plan.sound_effect_requirements:
            fingerprint = sound_effect_request_fingerprint(
                sound_effect_requirement,
                sample_rate_hz=self._configuration.sample_rate_hz,
                channel_count=self._configuration.channel_count,
                sample_width_bytes=self._configuration.sample_width_bytes,
            )
            requests.append(
                SoundEffectGenerationRequest(
                    request_id=f"sfx-request-{fingerprint[:24]}",
                    requirement_id=sound_effect_requirement.requirement_id,
                    cue_type=sound_effect_requirement.cue_type,
                    intensity=sound_effect_requirement.intensity,
                    duration_ms=sound_effect_requirement.target_duration_ms,
                    request_fingerprint=fingerprint,
                )
            )
        return tuple(requests)

    def _initial_manifest(
        self,
        *,
        command: StageCommand,
        source: ReadAudioDesignSourceScript,
        plan: AudioDesignPlan,
        requests: tuple[
            MusicGenerationRequest | SoundEffectGenerationRequest,
            ...,
        ],
    ) -> AudioDesignManifest:
        entries = tuple(
            AudioDesignManifestEntry(
                sequence_index=index,
                kind=(
                    AudioAssetKind.MUSIC
                    if isinstance(request, MusicGenerationRequest)
                    else AudioAssetKind.SOUND_EFFECT
                ),
                requirement_id=request.requirement_id,
                request_fingerprint=request.request_fingerprint,
                provider_id=(
                    self._music_provider.provider_id
                    if isinstance(request, MusicGenerationRequest)
                    else self._sound_effect_provider.provider_id
                ),
                expected_audio=self._format_expectation(request.duration_ms),
            )
            for index, request in enumerate(requests)
        )
        now = self._aware_now()
        return AudioDesignManifest(
            job_id=command.job_id,
            attempt_number=command.attempt_number,
            source_script_schema_version=source.schema_version,
            source_script_artifact_id=source.artifact_id,
            production_script_fingerprint=source.sha256,
            audio_design_plan_fingerprint=plan.plan_fingerprint,
            configuration_fingerprint=self._configuration.fingerprint(),
            music_provider_id=self._music_provider.provider_id,
            sound_effect_provider_id=self._sound_effect_provider.provider_id,
            expected_music_requirement_id=(
                plan.music_requirement.requirement_id
                if plan.music_requirement is not None
                else None
            ),
            expected_sound_effect_requirement_ids=tuple(
                item.requirement_id for item in plan.sound_effect_requirements
            ),
            entries=entries,
            summary=summarize_audio_design_entries(entries),
            status=AudioDesignManifestStatus.PREPARED,
            created_at=now,
            updated_at=now,
            metadata={
                "checkpointed": True,
                "final_mixing": False,
                "network": False,
                "simulated": True,
            },
        )

    def _validate_existing(
        self,
        *,
        manifest: AudioDesignManifest,
        source: ReadAudioDesignSourceScript,
        plan: AudioDesignPlan,
        command: StageCommand,
        requests: tuple[
            MusicGenerationRequest | SoundEffectGenerationRequest,
            ...,
        ],
    ) -> None:
        if (
            manifest.job_id != command.job_id
            or manifest.attempt_number != command.attempt_number
            or manifest.source_script_artifact_id != source.artifact_id
            or manifest.production_script_fingerprint != source.sha256
            or manifest.source_script_schema_version != source.schema_version
            or manifest.audio_design_plan_fingerprint != plan.plan_fingerprint
            or manifest.configuration_fingerprint != self._configuration.fingerprint()
            or tuple(entry.requirement_id for entry in manifest.entries)
            != tuple(request.requirement_id for request in requests)
            or tuple(entry.request_fingerprint for entry in manifest.entries)
            != tuple(request.request_fingerprint for request in requests)
        ):
            raise AudioDesignManifestError(
                "audio-design manifest source, plan, or configuration changed"
            )

    async def _checkpoint_generating(
        self,
        *,
        context: StageContext,
        manifest: AudioDesignManifest,
        entry: AudioDesignManifestEntry,
        increment: bool,
    ) -> tuple[AudioDesignManifest, AudioDesignManifestEntry]:
        generating = entry.model_copy(
            update={
                "status": AudioDesignAssetStatus.GENERATING,
                "generation_started_at": self._aware_now(),
                "generation_attempt_count": (
                    entry.generation_attempt_count + (1 if increment else 0)
                ),
                "error_code": None,
            }
        )
        current = replace_audio_design_entry(
            manifest,
            generating,
            status=AudioDesignManifestStatus.GENERATING,
            updated_at=self._aware_now(),
        )
        await self._manifest_store.checkpoint(
            context=context,
            previous=manifest,
            current=current,
        )
        return current, generating

    async def _generate(
        self,
        request: MusicGenerationRequest | SoundEffectGenerationRequest,
    ) -> GeneratedAudioResult:
        if isinstance(request, MusicGenerationRequest):
            return await self._music_provider.generate(request)
        return await self._sound_effect_provider.generate(request)

    def _expectation(
        self,
        job_id: UUID,
        request: MusicGenerationRequest | SoundEffectGenerationRequest,
        provider_id: str,
    ) -> AudioAssetExpectation:
        return AudioAssetExpectation(
            job_id=job_id,
            kind=(
                AudioAssetKind.MUSIC
                if isinstance(request, MusicGenerationRequest)
                else AudioAssetKind.SOUND_EFFECT
            ),
            requirement_id=request.requirement_id,
            request_fingerprint=request.request_fingerprint,
            provider_id=provider_id,
            audio=self._format_expectation(request.duration_ms),
        )

    def _format_expectation(self, duration_ms: int) -> AudioFormatExpectation:
        frames = frame_count_for_duration(
            duration_ms,
            self._configuration.sample_rate_hz,
        )
        return AudioFormatExpectation(
            duration_ms=duration_for_frame_count(
                frames,
                self._configuration.sample_rate_hz,
            ),
            frame_count=frames,
        )

    @staticmethod
    def _validate_result(
        result: GeneratedAudioResult,
        expected: AudioFormatExpectation,
        provider_id: str,
    ) -> None:
        actual: AudioPcmMetadata = result.audio
        if (
            result.provider_id != provider_id
            or result.media_type != "audio/wav"
            or not result.deterministic
            or actual.duration_ms != expected.duration_ms
            or actual.sample_rate_hz != expected.sample_rate_hz
            or actual.channel_count != expected.channel_count
            or actual.sample_width_bytes != expected.sample_width_bytes
            or actual.frame_count != expected.frame_count
            or hashlib.sha256(result.content).hexdigest() != result.sha256
        ):
            raise AudioDesignProviderResponseError(
                "audio-design provider result differs from request"
            )

    def _store_for(self, kind: AudioAssetKind) -> AudioDesignAssetStore:
        return self._music_store if kind is AudioAssetKind.MUSIC else self._sound_effect_store

    async def _recover_stored(
        self,
        entry: AudioDesignManifestEntry,
        expectation: AudioAssetExpectation,
        store: AudioDesignAssetStore,
    ) -> StoredAudioDesignAsset:
        resolved = await store.resolve(expectation=expectation)
        asset = resolved.asset
        if (
            entry.asset_id != asset.asset_id
            or entry.storage_path != asset.storage_path
            or entry.sha256 != asset.sha256
            or entry.size_bytes != asset.size_bytes
            or entry.expected_audio.duration_ms != asset.audio.duration_ms
            or entry.expected_audio.frame_count != asset.audio.frame_count
            or entry.request_fingerprint != asset.request_fingerprint
        ):
            raise AudioDesignStoreError("stored audio-design provenance changed")
        return asset

    def _stored_entry(
        self,
        *,
        entry: AudioDesignManifestEntry,
        asset: StoredAudioDesignAsset,
        recovered: bool,
    ) -> AudioDesignManifestEntry:
        return entry.model_copy(
            update={
                "status": AudioDesignAssetStatus.STORED,
                "generation_started_at": None,
                "stored_at": self._aware_now(),
                "asset_id": asset.asset_id,
                "artifact_id": _asset_artifact_id(
                    asset.job_id,
                    asset.requirement_id,
                    asset.request_fingerprint,
                ),
                "storage_path": asset.storage_path,
                "sha256": asset.sha256,
                "size_bytes": asset.size_bytes,
                "error_code": None,
                "metadata": {
                    "deterministic": True,
                    "recovered": recovered,
                    "simulated": True,
                },
            }
        )

    async def _checkpoint_failed(
        self,
        *,
        context: StageContext,
        manifest: AudioDesignManifest,
        entry: AudioDesignManifestEntry,
    ) -> None:
        failed = entry.model_copy(
            update={
                "status": AudioDesignAssetStatus.FAILED,
                "generation_started_at": None,
                "error_code": "simulated_generation_failed",
            }
        )
        current = replace_audio_design_entry(
            manifest,
            failed,
            status=AudioDesignManifestStatus.FAILED,
            updated_at=self._aware_now(),
        )
        await self._manifest_store.checkpoint(
            context=context,
            previous=manifest,
            current=current,
        )

    def _success(
        self,
        *,
        command: StageCommand,
        context: StageContext,
        source: ReadAudioDesignSourceScript,
        plan: AudioDesignPlan,
        manifest: AudioDesignManifest,
        assets: dict[str, StoredAudioDesignAsset],
        started_at: datetime,
    ) -> StageExecutionOutput:
        artifacts = [
            Artifact(
                artifact_id=_asset_artifact_id(
                    command.job_id,
                    entry.requirement_id,
                    entry.request_fingerprint,
                ),
                job_id=command.job_id,
                artifact_type=(
                    ArtifactType.MUSIC
                    if entry.kind is AudioAssetKind.MUSIC
                    else ArtifactType.SOUND_EFFECT
                ),
                relative_path=assets[entry.requirement_id].storage_path,
                mime_type="audio/wav",
                status=ArtifactStatus.READY,
                size_bytes=entry.size_bytes,
                sha256=entry.sha256,
                duration_seconds=entry.expected_audio.duration_ms / 1_000,
                provider=entry.provider_id,
                model_version="simulated-audio-design-v1",
                metadata={
                    "kind": entry.kind.value,
                    "requirement_id": entry.requirement_id,
                    "request_fingerprint": entry.request_fingerprint,
                    "sample_rate_hz": entry.expected_audio.sample_rate_hz,
                    "channel_count": entry.expected_audio.channel_count,
                    "sample_width_bytes": entry.expected_audio.sample_width_bytes,
                    "simulated": True,
                },
            )
            for entry in manifest.entries
        ]
        content = serialize_audio_design_manifest(manifest)
        artifacts.append(
            Artifact(
                artifact_id=_manifest_artifact_id(
                    command.job_id,
                    command.attempt_number,
                ),
                job_id=command.job_id,
                artifact_type=ArtifactType.PRODUCTION_AUDIO_DESIGN_MANIFEST,
                relative_path=audio_design_manifest_relative_path(context),
                mime_type="application/json",
                status=ArtifactStatus.READY,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                provider="orion-simulated-audio-design",
                model_version="1.0.0",
                metadata={
                    "music_count": manifest.summary.music_assets,
                    "sound_effect_count": manifest.summary.sound_effect_assets,
                    "source_script_artifact_id": str(source.artifact_id),
                    "source_script_sha256": source.sha256,
                    "plan_fingerprint": plan.plan_fingerprint,
                    "schema_version": manifest.schema_version,
                    "status": manifest.status.value,
                    "simulated": True,
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
                    "music_count": manifest.summary.music_assets,
                    "sound_effect_count": manifest.summary.sound_effect_assets,
                    "simulated": True,
                    "checkpointed": True,
                    "final_mixing": False,
                },
            ),
            artifacts=tuple(artifacts),
        )

    def _failure(
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
                error_message="Audio-design stage did not complete",
                retry_after_seconds=retry_after_seconds,
                metadata={
                    "handler": type(self).__name__,
                    "simulated": True,
                    "network": False,
                },
            )
        )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("audio-design clock must be timezone-aware")
        return value


def _entry_for(
    manifest: AudioDesignManifest,
    requirement_id: str,
) -> AudioDesignManifestEntry:
    try:
        return next(entry for entry in manifest.entries if entry.requirement_id == requirement_id)
    except StopIteration as exc:
        raise AudioDesignManifestError("audio-design manifest requirement is missing") from exc


def _asset_artifact_id(
    job_id: UUID,
    requirement_id: str,
    request_fingerprint: str,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orion:audio-design:{job_id}:{requirement_id}:{request_fingerprint}",
    )


def _manifest_artifact_id(job_id: UUID, attempt_number: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orion:audio-design-manifest:{job_id}:{attempt_number}",
    )
