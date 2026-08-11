"""Read, normalize, and verify all durable inputs for composition."""

from __future__ import annotations

import asyncio
import hashlib
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePosixPath
from typing import Protocol, cast
from uuid import UUID

from backend.src.production.audio_design.configuration import (
    AudioDesignConfiguration,
)
from backend.src.production.audio_design.models import (
    AudioAssetKind,
    AudioDesignAssetStatus,
    AudioDesignManifest,
    AudioDesignManifestEntry,
    AudioDesignManifestStatus,
)
from backend.src.production.audio_design.plan import derive_audio_design_plan
from backend.src.production.audio_design.serialization import (
    deserialize_audio_design_manifest,
)
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetError,
    BinaryAssetNotFoundError,
)
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType
from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.media_composition.domain.fingerprints import (
    canonical_sha256,
)
from backend.src.production.media_composition.domain.hybrid import (
    HybridImageMotionCompositionPlan,
    HybridImageMotionRenderResult,
    deserialize_hybrid_image_motion_plan,
)
from backend.src.production.media_composition.domain.models import (
    CompositionAssetAvailability,
    CompositionAssetKind,
    CompositionAssetReference,
    CompositionAssetValidation,
    CompositionTransitionKind,
    SourceManifestReference,
)
from backend.src.production.media_composition.exceptions import (
    MediaCompositionSourceError,
)
from backend.src.production.media_composition.ports import (
    CompositionMusicSource,
    CompositionNarrationSource,
    CompositionShotSource,
    CompositionSoundEffectSource,
    CompositionSubtitleSource,
    MediaCompositionArtifactInventory,
    MediaCompositionSource,
    MediaCompositionStageContext,
)
from backend.src.production.planning.provider_budget_planner import SceneProviderPurchasePlan
from backend.src.production.runtime.context import StageContext
from backend.src.production.scene_planning.models import ProductionScenePlan
from backend.src.production.scene_planning.ports import (
    ProductionScriptReader,
    ReadProductionScript,
)
from backend.src.production.speech_generation.models import (
    SpeechGenerationManifest,
    SpeechGenerationManifestStatus,
    SpeechSegmentManifestEntry,
    SpeechSegmentStatus,
)
from backend.src.production.speech_generation.serialization import (
    deserialize_speech_manifest,
)
from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipEntry,
    ProductionVideoClipManifest,
    VideoClipEntryStatus,
    VideoClipManifestStatus,
)
from backend.src.production.video_clip_generation.serialization import (
    deserialize_video_clip_manifest,
)
from backend.src.production.visual_asset_planning.models import VisualAssetRole
from backend.src.production.visual_asset_planning.ports import (
    ProductionScenePlanReader,
    ReadProductionScenePlan,
)

_SRT_TIMING = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})"
    r" --> "
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})$"
)
_TRANSITIONS = {
    "none": CompositionTransitionKind.NONE,
    "cut": CompositionTransitionKind.CUT,
    "dissolve": CompositionTransitionKind.DISSOLVE,
    "fade": CompositionTransitionKind.FADE,
    "wipe": CompositionTransitionKind.WIPE,
    "match_cut": CompositionTransitionKind.MATCH_CUT,
}


class ScenePlanReader(Protocol):
    async def read_for_visual_asset_planning(
        self,
        *,
        context: StageContext,
    ) -> ReadProductionScenePlan: ...


class DurableMediaCompositionSourceReader:
    def __init__(
        self,
        *,
        workspace_root: Path,
        inventory: MediaCompositionArtifactInventory,
        script_reader: ProductionScriptReader,
        scene_plan_reader: ProductionScenePlanReader | ScenePlanReader,
        audio_design_configuration: AudioDesignConfiguration,
        configuration: MediaCompositionConfiguration,
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._inventory = inventory
        self._script_reader = script_reader
        self._scene_plan_reader = scene_plan_reader
        self._audio_configuration = audio_design_configuration
        self._configuration = configuration

    async def read(
        self,
        *,
        context: MediaCompositionStageContext,
    ) -> MediaCompositionSource:
        stage_context = cast(StageContext, context)
        inventory_context = stage_context.model_copy(update={"input_artifact_ids": ()})
        script_source, scene_source, artifacts = await asyncio.gather(
            self._script_reader.read_for_scene_planning(context=inventory_context),
            self._scene_plan_reader.read_for_visual_asset_planning(context=inventory_context),
            self._inventory.list_for_job(context.job_id),
        )
        return await asyncio.to_thread(
            self._read_sync,
            context,
            script_source,
            scene_source,
            artifacts,
        )

    def _read_sync(
        self,
        context: MediaCompositionStageContext,
        script_source: ReadProductionScript,
        scene_source: ReadProductionScenePlan,
        artifacts: tuple[Artifact, ...],
    ) -> MediaCompositionSource:
        by_type = _by_type(artifacts)
        hybrid = bool(by_type.get(ArtifactType.HYBRID_IMAGE_MOTION_RENDER_RESULT))
        video_artifact: Artifact | None = None
        video_content: bytes | None = None
        hybrid_plan_artifact: Artifact | None = None
        hybrid_result_artifact: Artifact | None = None
        hybrid_plan: HybridImageMotionCompositionPlan | None = None
        hybrid_result: HybridImageMotionRenderResult | None = None
        if hybrid:
            hybrid_plan_artifact, hybrid_plan_content = self._selected_manifest(
                by_type,
                ArtifactType.HYBRID_IMAGE_MOTION_COMPOSITION_PLAN,
                context.job_id,
            )
            hybrid_result_artifact, hybrid_result_content = self._selected_manifest(
                by_type,
                ArtifactType.HYBRID_IMAGE_MOTION_RENDER_RESULT,
                context.job_id,
            )
            try:
                hybrid_plan = deserialize_hybrid_image_motion_plan(hybrid_plan_content)
                hybrid_result = HybridImageMotionRenderResult.model_validate_json(
                    hybrid_result_content
                )
            except (TypeError, ValueError) as exc:
                raise MediaCompositionSourceError("hybrid visual artifacts are invalid") from exc
        else:
            video_artifact, video_content = self._selected_manifest(
                by_type,
                ArtifactType.PRODUCTION_VIDEO_CLIP_MANIFEST,
                context.job_id,
            )
        speech_artifact, speech_content = self._selected_manifest(
            by_type,
            ArtifactType.PRODUCTION_SPEECH_GENERATION_MANIFEST,
            context.job_id,
        )
        audio_artifact, audio_content = self._selected_manifest(
            by_type,
            ArtifactType.PRODUCTION_AUDIO_DESIGN_MANIFEST,
            context.job_id,
        )
        try:
            video = (
                deserialize_video_clip_manifest(video_content)
                if video_content is not None
                else None
            )
            speech = deserialize_speech_manifest(speech_content)
            audio = deserialize_audio_design_manifest(audio_content)
        except (TypeError, ValueError) as exc:
            raise MediaCompositionSourceError("an upstream media manifest is invalid") from exc
        if video is not None:
            self._validate_manifests(
                context.job_id,
                script_source,
                scene_source.scene_plan,
                video,
                speech,
                audio,
            )
        else:
            self._validate_non_video_manifests(
                context.job_id,
                script_source,
                scene_source.scene_plan,
                speech,
                audio,
            )
        audio_plan = derive_audio_design_plan(
            job_id=context.job_id,
            source_script_artifact_id=script_source.artifact_id,
            source_script_sha256=script_source.sha256,
            script=script_source.script,
            configuration=self._audio_configuration,
        )
        if audio.audio_design_plan_fingerprint != audio_plan.plan_fingerprint:
            raise MediaCompositionSourceError("audio-design plan differs from its durable manifest")
        registered = {item.artifact_id: item for item in artifacts}
        expected: list[CompositionAssetReference] = []
        validation: list[CompositionAssetValidation] = []

        video_by_shot: dict[str, CompositionAssetReference] = {}
        hybrid_asset: CompositionAssetReference | None = None
        if video is not None:
            for video_entry in video.entries:
                if video_entry.role is not VisualAssetRole.PRIMARY:
                    continue
                if video_entry.source_shot_id in video_by_shot:
                    raise MediaCompositionSourceError(
                        "multiple primary video clips exist for one shot"
                    )
                video_asset = _video_asset(video_entry)
                video_by_shot[video_entry.source_shot_id] = video_asset
                expected.append(video_asset)
                validation.append(self._validate_asset(video_asset, registered))
        else:
            assert hybrid_plan is not None and hybrid_result is not None
            hybrid_asset = self._hybrid_visual_asset(
                by_type,
                context.job_id,
                hybrid_result,
            )
            expected.append(hybrid_asset)
            validation.append(self._validate_asset(hybrid_asset, registered))

        scene_starts: dict[str, int] = {}
        shots: list[CompositionShotSource] = []
        purchase_by_scene = (
            {item.scene_id: item for item in video.purchase_plan.scenes}
            if video is not None and video.purchase_plan is not None
            else {}
        )
        if hybrid_plan is not None and hybrid_asset is not None:
            for segment in hybrid_plan.segments:
                scene_id = segment.shot_id.rsplit("-shot-", 1)[0]
                scene_number = int(scene_id.removeprefix("scene-"))
                shot_number = int(segment.shot_id.rsplit("-shot-", 1)[1])
                scene_starts.setdefault(scene_id, segment.timeline_start_ms)
                shots.append(
                    CompositionShotSource(
                        scene_id=scene_id,
                        shot_id=segment.shot_id,
                        scene_number=scene_number,
                        shot_number=shot_number,
                        scene_start_ms=scene_starts[scene_id],
                        shot_start_ms=segment.timeline_start_ms,
                        shot_end_ms=segment.timeline_end_ms,
                        transition_kind=CompositionTransitionKind.CUT,
                        transition_duration_ms=0,
                        video_asset_id=hybrid_asset.asset_id,
                        source_start_ms=segment.timeline_start_ms,
                    )
                )
        else:
            self._append_legacy_shots(
                shots=shots,
                scene_starts=scene_starts,
                scene_plan=scene_source.scene_plan,
                video_by_shot=video_by_shot,
                purchase_by_scene=purchase_by_scene,
            )
        narration: list[CompositionNarrationSource] = []
        for speech_entry in speech.entries:
            speech_asset = _speech_asset(speech_entry)
            expected.append(speech_asset)
            validation.append(self._validate_asset(speech_asset, registered))
            start = scene_starts.get(speech_entry.source_scene_id)
            if start is None or speech_entry.duration_ms is None:
                raise MediaCompositionSourceError("narration scene or duration is invalid")
            narration.append(
                CompositionNarrationSource(
                    scene_id=speech_entry.source_scene_id,
                    sequence_index=speech_entry.sequence_index,
                    timeline_start_ms=start,
                    duration_ms=speech_entry.duration_ms,
                    asset_id=speech_asset.asset_id,
                )
            )

        audio_entries = {entry.requirement_id: entry for entry in audio.entries}
        music: CompositionMusicSource | None = None
        if audio_plan.music_requirement is not None:
            music_requirement = audio_plan.music_requirement
            music_entry = audio_entries.get(music_requirement.requirement_id)
            if music_entry is None:
                raise MediaCompositionSourceError("music manifest entry is missing")
            music_asset = _audio_asset(music_entry)
            expected.append(music_asset)
            validation.append(self._validate_asset(music_asset, registered))
            music = CompositionMusicSource(
                requirement_id=music_requirement.requirement_id,
                duration_ms=music_requirement.target_duration_ms,
                duck_under_narration=music_requirement.duck_under_narration,
                asset_id=music_asset.asset_id,
            )
        sound_effects: list[CompositionSoundEffectSource] = []
        for sound_effect_requirement in audio_plan.sound_effect_requirements:
            sound_effect_entry = audio_entries.get(sound_effect_requirement.requirement_id)
            if sound_effect_entry is None:
                raise MediaCompositionSourceError("sound-effect manifest entry is missing")
            sound_effect_asset = _audio_asset(sound_effect_entry)
            expected.append(sound_effect_asset)
            validation.append(self._validate_asset(sound_effect_asset, registered))
            sound_effects.append(
                CompositionSoundEffectSource(
                    requirement_id=sound_effect_requirement.requirement_id,
                    scene_id=sound_effect_requirement.scene_id,
                    shot_id=sound_effect_requirement.shot_id,
                    target_offset_ms=sound_effect_requirement.target_offset_ms,
                    duration_ms=sound_effect_requirement.target_duration_ms,
                    asset_id=sound_effect_asset.asset_id,
                )
            )

        subtitle, subtitle_asset = self._subtitle_source(
            by_type.get(ArtifactType.SUBTITLES, ()),
            context.job_id,
        )
        if subtitle_asset is not None:
            expected.append(subtitle_asset)
            validation.append(self._validate_asset(subtitle_asset, registered))

        expected = sorted(expected, key=lambda item: item.asset_id)
        validation = sorted(validation, key=lambda item: item.asset_id)
        expected_artifact_ids = {item.artifact_id for item in expected}
        relevant = {
            ArtifactType.SOURCE_VIDEO_CLIP,
            ArtifactType.NARRATION,
            ArtifactType.MUSIC,
            ArtifactType.SOUND_EFFECT,
            ArtifactType.SUBTITLES,
        }
        orphans = tuple(
            sorted(
                str(item.artifact_id)
                for item in artifacts
                if item.artifact_type in relevant
                and item.size_bytes not in {None, 0}
                and item.artifact_id not in expected_artifact_ids
            )
        )
        manifest_references = [
            _source_reference(
                        script_source.artifact_id,
                        ArtifactType.PRODUCTION_SCRIPT,
                        script_source.relative_path,
                        script_source.schema_version,
                        script_source.sha256,
                        script_source.size_bytes,
                    ),
            _source_reference(
                        scene_source.artifact_id,
                        ArtifactType.PRODUCTION_SCENE_PLAN,
                        scene_source.relative_path,
                        scene_source.schema_version,
                        scene_source.sha256,
                        scene_source.size_bytes,
                    ),
            _artifact_reference(speech_artifact, speech.schema_version),
            _artifact_reference(audio_artifact, audio.schema_version),
        ]
        if video_artifact is not None and video is not None:
            manifest_references.append(_artifact_reference(video_artifact, video.schema_version))
        else:
            assert hybrid_plan_artifact is not None and hybrid_result_artifact is not None
            manifest_references.extend(
                (
                    _artifact_reference(hybrid_plan_artifact, "1.0.0"),
                    _artifact_reference(hybrid_result_artifact, "1.0.0"),
                )
            )
        source_manifests = tuple(
            sorted(manifest_references, key=lambda item: item.artifact_type.value)
        )
        return MediaCompositionSource(
            job_id=context.job_id,
            source_manifests=source_manifests,
            assets=tuple(expected),
            asset_validation=tuple(validation),
            shots=tuple(shots),
            narration=tuple(narration),
            music=music,
            sound_effects=tuple(sound_effects),
            subtitles=subtitle,
            orphan_asset_ids=orphans,
        )

    @staticmethod
    def _append_legacy_shots(
        *,
        shots: list[CompositionShotSource],
        scene_starts: dict[str, int],
        scene_plan: ProductionScenePlan,
        video_by_shot: dict[str, CompositionAssetReference],
        purchase_by_scene: dict[str, SceneProviderPurchasePlan],
    ) -> None:
        global_scene_start = 0
        for scene in scene_plan.scenes:
            scene_starts[scene.scene_id] = global_scene_start
            purchased = purchase_by_scene.get(scene.scene_id)
            if purchased is not None:
                local_start = 0
                for index, clip in enumerate(purchased.clips):
                    shot_asset = video_by_shot.get(clip.shot_id)
                    if shot_asset is None:
                        raise MediaCompositionSourceError(
                            "a purchased visual shot has no primary video clip"
                        )
                    local_end = local_start + clip.usable_duration_ms
                    final = index == len(purchased.clips) - 1
                    source_transition = scene.shots[-1].transition
                    shots.append(
                        CompositionShotSource(
                            scene_id=scene.scene_id,
                            shot_id=clip.shot_id,
                            scene_number=scene.scene_number,
                            shot_number=index + 1,
                            scene_start_ms=global_scene_start,
                            shot_start_ms=global_scene_start + local_start,
                            shot_end_ms=global_scene_start + local_end,
                            transition_kind=(
                                _TRANSITIONS[source_transition.kind]
                                if final
                                else _TRANSITIONS["cut"]
                            ),
                            transition_duration_ms=(
                                _seconds_to_ms(source_transition.duration_seconds)
                                if final
                                else 0
                            ),
                            video_asset_id=shot_asset.asset_id,
                        )
                    )
                    local_start = local_end
                if local_start != purchased.resolved_duration_ms:
                    raise MediaCompositionSourceError(
                        "purchased visual shots do not cover their narrative scene"
                    )
                global_scene_start += purchased.resolved_duration_ms
                continue
            for shot in scene.shots:
                shot_asset = video_by_shot.get(shot.shot_id)
                if shot_asset is None:
                    raise MediaCompositionSourceError(
                        "a scene-plan shot has no primary video clip"
                    )
                start_ms = global_scene_start + _seconds_to_ms(shot.timing.start_seconds)
                end_ms = global_scene_start + _seconds_to_ms(shot.timing.end_seconds)
                shots.append(
                    CompositionShotSource(
                        scene_id=scene.scene_id,
                        shot_id=shot.shot_id,
                        scene_number=scene.scene_number,
                        shot_number=shot.shot_number,
                        scene_start_ms=global_scene_start,
                        shot_start_ms=start_ms,
                        shot_end_ms=end_ms,
                        transition_kind=_TRANSITIONS[shot.transition.kind],
                        transition_duration_ms=_seconds_to_ms(
                            shot.transition.duration_seconds
                        ),
                        video_asset_id=shot_asset.asset_id,
                    )
                )
            global_scene_start += _seconds_to_ms(scene.estimated_duration_seconds)

    def _hybrid_visual_asset(
        self,
        by_type: dict[ArtifactType, tuple[Artifact, ...]],
        job_id: UUID,
        result: HybridImageMotionRenderResult,
    ) -> CompositionAssetReference:
        candidates = tuple(
            item
            for item in by_type.get(ArtifactType.SOURCE_VIDEO_CLIP, ())
            if item.relative_path == result.output_relative_path
            and item.metadata.get("hybrid_visual_track") is True
        )
        if len(candidates) != 1:
            raise MediaCompositionSourceError("hybrid visual track artifact is ambiguous")
        artifact = candidates[0]
        if (
            artifact.job_id != job_id
            or artifact.sha256 != result.output_sha256
            or artifact.size_bytes != result.size_bytes
        ):
            raise MediaCompositionSourceError("hybrid visual track provenance differs")
        return CompositionAssetReference(
            asset_id=f"hybrid-visual-track-{result.output_sha256[:24]}",
            artifact_id=artifact.artifact_id,
            kind=CompositionAssetKind.VIDEO,
            relative_path=result.output_relative_path,
            mime_type="video/mp4",
            sha256=result.output_sha256,
            fingerprint=canonical_sha256(
                {
                    "execution_fingerprint": result.execution_fingerprint,
                    "sha256": result.output_sha256,
                }
            ),
            size_bytes=result.size_bytes,
            duration_ms=result.duration_ms,
            width=result.width,
            height=result.height,
            frame_rate=result.frame_rate,
            frame_count=result.frame_count,
        )

    def _selected_manifest(
        self,
        by_type: dict[ArtifactType, tuple[Artifact, ...]],
        artifact_type: ArtifactType,
        job_id: UUID,
    ) -> tuple[Artifact, bytes]:
        candidates = by_type.get(artifact_type, ())
        if not candidates:
            raise MediaCompositionSourceError(f"required {artifact_type.value} is not registered")
        selected = max(
            candidates,
            key=lambda item: (_attempt(item.relative_path), str(item.artifact_id)),
        )
        return selected, self._verified_content(selected, job_id)

    def _verified_content(self, artifact: Artifact, job_id: UUID) -> bytes:
        if (
            artifact.job_id != job_id
            or artifact.status is not ArtifactStatus.READY
            or artifact.size_bytes is None
            or artifact.size_bytes <= 0
            or artifact.sha256 is None
            or artifact.size_bytes > self._configuration.max_source_manifest_bytes
        ):
            raise MediaCompositionSourceError("source artifact integrity metadata is incomplete")
        if not artifact.relative_path.startswith(f"production/{job_id}/"):
            raise MediaCompositionSourceError("source artifact path is not contractual")
        try:
            target = self._confinement.resolve(
                artifact.relative_path,
                require_exists=True,
            )
            self._confinement.reject_unsafe_file(target)
            with target.open("rb") as stream:
                content = stream.read(self._configuration.max_source_manifest_bytes + 1)
        except (BinaryAssetError, OSError) as exc:
            raise MediaCompositionSourceError("source artifact could not be read") from exc
        if (
            len(content) != artifact.size_bytes
            or len(content) > self._configuration.max_source_manifest_bytes
            or hashlib.sha256(content).hexdigest() != artifact.sha256
        ):
            raise MediaCompositionSourceError("source artifact checksum or size differs")
        return content

    def _validate_asset(
        self,
        expected: CompositionAssetReference,
        registered: dict[UUID, Artifact],
    ) -> CompositionAssetValidation:
        record = registered.get(expected.artifact_id)
        if (
            record is None
            or record.relative_path != expected.relative_path
            or record.sha256 != expected.sha256
            or record.size_bytes != expected.size_bytes
        ):
            return CompositionAssetValidation(
                asset_id=expected.asset_id,
                availability=CompositionAssetAvailability.MISSING,
                relative_path=expected.relative_path,
                expected_sha256=expected.sha256,
                issue_code="artifact_registry_mismatch",
            )
        try:
            target = self._confinement.resolve(
                expected.relative_path,
                require_exists=True,
            )
            self._confinement.reject_unsafe_file(target)
            if target.stat().st_size != expected.size_bytes:
                raise ValueError
            digest = _hash_file(target, expected.size_bytes)
        except (BinaryAssetNotFoundError, FileNotFoundError):
            return CompositionAssetValidation(
                asset_id=expected.asset_id,
                availability=CompositionAssetAvailability.MISSING,
                relative_path=expected.relative_path,
                expected_sha256=expected.sha256,
                issue_code="asset_missing",
            )
        except Exception:
            return CompositionAssetValidation(
                asset_id=expected.asset_id,
                availability=CompositionAssetAvailability.CORRUPT,
                relative_path=expected.relative_path,
                expected_sha256=expected.sha256,
                issue_code="asset_unsafe_or_unreadable",
            )
        if digest != expected.sha256:
            return CompositionAssetValidation(
                asset_id=expected.asset_id,
                availability=CompositionAssetAvailability.CORRUPT,
                relative_path=expected.relative_path,
                expected_sha256=expected.sha256,
                actual_sha256=digest,
                issue_code="asset_checksum_mismatch",
            )
        return CompositionAssetValidation(
            asset_id=expected.asset_id,
            availability=CompositionAssetAvailability.AVAILABLE,
            relative_path=expected.relative_path,
            expected_sha256=expected.sha256,
            actual_sha256=digest,
        )

    def _subtitle_source(
        self,
        candidates: tuple[Artifact, ...],
        job_id: UUID,
    ) -> tuple[CompositionSubtitleSource | None, CompositionAssetReference | None]:
        durable = tuple(
            item
            for item in candidates
            if item.status is ArtifactStatus.READY
            and item.size_bytes is not None
            and item.size_bytes > 0
            and item.sha256 is not None
        )
        if not durable:
            return None, None
        selected = max(
            durable,
            key=lambda item: (_attempt(item.relative_path), str(item.artifact_id)),
        )
        content = self._verified_content(selected, job_id)
        starts, ends, hashes = _parse_srt(content)
        subtitle_sha = cast(str, selected.sha256)
        asset_id = f"subtitles-{subtitle_sha[:24]}"
        asset = CompositionAssetReference(
            asset_id=asset_id,
            artifact_id=selected.artifact_id,
            kind=CompositionAssetKind.SUBTITLES,
            relative_path=selected.relative_path,
            mime_type=selected.mime_type,
            sha256=subtitle_sha,
            fingerprint=canonical_sha256(
                {
                    "artifact_id": str(selected.artifact_id),
                    "sha256": selected.sha256,
                    "size_bytes": selected.size_bytes,
                }
            ),
            size_bytes=cast(int, selected.size_bytes),
        )
        return (
            CompositionSubtitleSource(
                asset_id=asset_id,
                cue_start_ms=starts,
                cue_end_ms=ends,
                cue_text_sha256=hashes,
            ),
            asset,
        )

    @staticmethod
    def _validate_manifests(
        job_id: UUID,
        script: ReadProductionScript,
        scene_plan: ProductionScenePlan,
        video: ProductionVideoClipManifest,
        speech: SpeechGenerationManifest,
        audio: AudioDesignManifest,
    ) -> None:
        if (
            scene_plan.source_script_sha256 != script.sha256
            or video.status is not VideoClipManifestStatus.COMPLETED
            or speech.status is not SpeechGenerationManifestStatus.COMPLETED
            or audio.status is not AudioDesignManifestStatus.COMPLETE
            or speech.job_id != job_id
            or audio.job_id != job_id
            or speech.source_script_artifact_id != script.artifact_id
            or speech.source_script_sha256 != script.sha256
            or audio.source_script_artifact_id != script.artifact_id
            or audio.production_script_fingerprint != script.sha256
            or any(entry.status is not VideoClipEntryStatus.STORED for entry in video.entries)
            or any(entry.status is not SpeechSegmentStatus.STORED for entry in speech.entries)
            or any(entry.status is not AudioDesignAssetStatus.STORED for entry in audio.entries)
        ):
            raise MediaCompositionSourceError("upstream manifests are incomplete or incompatible")

    @staticmethod
    def _validate_non_video_manifests(
        job_id: UUID,
        script: ReadProductionScript,
        scene_plan: ProductionScenePlan,
        speech: SpeechGenerationManifest,
        audio: AudioDesignManifest,
    ) -> None:
        if (
            scene_plan.source_script_sha256 != script.sha256
            or speech.status is not SpeechGenerationManifestStatus.COMPLETED
            or audio.status is not AudioDesignManifestStatus.COMPLETE
            or speech.job_id != job_id
            or audio.job_id != job_id
            or speech.source_script_artifact_id != script.artifact_id
            or speech.source_script_sha256 != script.sha256
            or audio.source_script_artifact_id != script.artifact_id
            or audio.production_script_fingerprint != script.sha256
            or any(entry.status is not SpeechSegmentStatus.STORED for entry in speech.entries)
            or any(entry.status is not AudioDesignAssetStatus.STORED for entry in audio.entries)
        ):
            raise MediaCompositionSourceError("upstream manifests are incomplete or incompatible")


def _video_asset(entry: ProductionVideoClipEntry) -> CompositionAssetReference:
    values = {
        name: getattr(entry, name)
        for name in (
            "video_binary_asset_id",
            "video_artifact_id",
            "storage_path",
            "mime_type",
            "sha256",
            "size_bytes",
            "duration_seconds",
            "width",
            "height",
            "frame_rate",
            "frame_count",
        )
    }
    if any(value is None for value in values.values()):
        raise MediaCompositionSourceError("stored video entry metadata is incomplete")
    frame_rate = cast(float, values["frame_rate"])
    if not frame_rate.is_integer():
        raise MediaCompositionSourceError("fractional frame rates are not supported")
    duration_ms = _seconds_to_ms(cast(float, values["duration_seconds"]))
    if abs(cast(int, values["frame_count"]) - _ms_to_frames(duration_ms, int(frame_rate))) > 1:
        raise MediaCompositionSourceError("video duration and frame count differ")
    return CompositionAssetReference(
        asset_id=cast(str, values["video_binary_asset_id"]),
        artifact_id=cast(UUID, values["video_artifact_id"]),
        kind=CompositionAssetKind.VIDEO,
        relative_path=cast(str, values["storage_path"]),
        mime_type=cast(str, values["mime_type"]),
        sha256=cast(str, values["sha256"]),
        fingerprint=canonical_sha256(
            {
                "asset_id": values["video_binary_asset_id"],
                "frame_count": values["frame_count"],
                "frame_rate": int(frame_rate),
                "sha256": values["sha256"],
                "size_bytes": values["size_bytes"],
            }
        ),
        size_bytes=cast(int, values["size_bytes"]),
        duration_ms=duration_ms,
        width=cast(int, values["width"]),
        height=cast(int, values["height"]),
        frame_rate=int(frame_rate),
        frame_count=cast(int, values["frame_count"]),
        scene_id=entry.source_scene_id,
        shot_id=entry.source_shot_id,
    )


def _speech_asset(entry: SpeechSegmentManifestEntry) -> CompositionAssetReference:
    required = {
        name: getattr(entry, name)
        for name in (
            "audio_binary_asset_id",
            "audio_artifact_id",
            "storage_path",
            "mime_type",
            "sha256",
            "size_bytes",
            "duration_ms",
            "sample_rate_hz",
            "channel_count",
            "sample_width_bytes",
            "frame_count",
            "normalized_text_hash",
        )
    }
    if any(value is None for value in required.values()):
        raise MediaCompositionSourceError("stored narration metadata is incomplete")
    return CompositionAssetReference(
        asset_id=cast(str, required["audio_binary_asset_id"]),
        artifact_id=cast(UUID, required["audio_artifact_id"]),
        kind=CompositionAssetKind.NARRATION,
        relative_path=cast(str, required["storage_path"]),
        mime_type=cast(str, required["mime_type"]),
        sha256=cast(str, required["sha256"]),
        fingerprint=canonical_sha256(
            {
                "normalized_text_hash": required["normalized_text_hash"],
                "sha256": required["sha256"],
                "segment_id": entry.segment_id,
            }
        ),
        size_bytes=cast(int, required["size_bytes"]),
        duration_ms=cast(int, required["duration_ms"]),
        frame_count=cast(int, required["frame_count"]),
        sample_rate_hz=cast(int, required["sample_rate_hz"]),
        channel_count=cast(int, required["channel_count"]),
        sample_width_bytes=cast(int, required["sample_width_bytes"]),
        scene_id=entry.source_scene_id,
        shot_id=entry.source_shot_id,
    )


def _audio_asset(entry: AudioDesignManifestEntry) -> CompositionAssetReference:
    if (
        entry.status is not AudioDesignAssetStatus.STORED
        or entry.asset_id is None
        or entry.artifact_id is None
        or entry.storage_path is None
        or entry.sha256 is None
        or entry.size_bytes is None
    ):
        raise MediaCompositionSourceError("stored audio-design metadata is incomplete")
    expected = entry.expected_audio
    kind = entry.kind
    return CompositionAssetReference(
        asset_id=entry.asset_id,
        artifact_id=entry.artifact_id,
        kind=(
            CompositionAssetKind.MUSIC
            if kind is AudioAssetKind.MUSIC
            else CompositionAssetKind.SOUND_EFFECT
        ),
        relative_path=entry.storage_path,
        mime_type="audio/wav",
        sha256=entry.sha256,
        fingerprint=entry.request_fingerprint,
        size_bytes=entry.size_bytes,
        duration_ms=expected.duration_ms,
        frame_count=expected.frame_count,
        sample_rate_hz=expected.sample_rate_hz,
        channel_count=expected.channel_count,
        sample_width_bytes=expected.sample_width_bytes,
    )


def _by_type(
    artifacts: tuple[Artifact, ...],
) -> dict[ArtifactType, tuple[Artifact, ...]]:
    return {
        artifact_type: tuple(item for item in artifacts if item.artifact_type is artifact_type)
        for artifact_type in ArtifactType
    }


def _artifact_reference(
    artifact: Artifact,
    schema_version: str,
) -> SourceManifestReference:
    if artifact.sha256 is None or artifact.size_bytes is None:
        raise MediaCompositionSourceError("source manifest metadata is incomplete")
    return _source_reference(
        artifact.artifact_id,
        artifact.artifact_type,
        artifact.relative_path,
        schema_version,
        artifact.sha256,
        artifact.size_bytes,
    )


def _source_reference(
    artifact_id: UUID,
    artifact_type: ArtifactType,
    relative_path: str,
    schema_version: str,
    sha256: str,
    size_bytes: int,
) -> SourceManifestReference:
    return SourceManifestReference(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        relative_path=relative_path,
        schema_version=schema_version,
        sha256=sha256,
        size_bytes=size_bytes,
    )


def _attempt(relative_path: str) -> int:
    for part in PurePosixPath(relative_path).parts:
        if part.startswith("attempt-") and part[8:].isdigit():
            return int(part[8:])
    return -1


def _seconds_to_ms(value: float) -> int:
    return int((Decimal(str(value)) * Decimal(1_000)).to_integral_value(rounding=ROUND_HALF_UP))


def _ms_to_frames(milliseconds: int, frame_rate: int) -> int:
    return (milliseconds * frame_rate + 500) // 1_000


def _hash_file(path: Path, expected_size: int) -> str:
    digest = hashlib.sha256()
    remaining = expected_size
    with path.open("rb") as stream:
        while remaining:
            chunk = stream.read(min(1_048_576, remaining))
            if not chunk:
                raise ValueError("asset ended before expected size")
            digest.update(chunk)
            remaining -= len(chunk)
        if stream.read(1):
            raise ValueError("asset exceeds expected size")
    return digest.hexdigest()


def _parse_srt(content: bytes) -> tuple[tuple[int, ...], tuple[int, ...], tuple[str, ...]]:
    try:
        text = content.decode("utf-8", errors="strict").replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        raise MediaCompositionSourceError("subtitle file is not strict UTF-8") from exc
    blocks = tuple(block for block in text.strip().split("\n\n") if block)
    starts: list[int] = []
    ends: list[int] = []
    hashes: list[str] = []
    for expected_index, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(expected_index):
            raise MediaCompositionSourceError("subtitle cue ordering is invalid")
        match = _SRT_TIMING.fullmatch(lines[1])
        if match is None:
            raise MediaCompositionSourceError("subtitle timestamp is invalid")
        start = _srt_ms(match, "s")
        end = _srt_ms(match, "e")
        cue_text = "\n".join(lines[2:])
        if not cue_text or end <= start:
            raise MediaCompositionSourceError("subtitle cue is empty or negative")
        starts.append(start)
        ends.append(end)
        hashes.append(hashlib.sha256(cue_text.encode("utf-8")).hexdigest())
    if not blocks:
        raise MediaCompositionSourceError("subtitle file is empty")
    return tuple(starts), tuple(ends), tuple(hashes)


def _srt_ms(match: re.Match[str], prefix: str) -> int:
    return (
        int(match.group(f"{prefix}h")) * 3_600_000
        + int(match.group(f"{prefix}m")) * 60_000
        + int(match.group(f"{prefix}s")) * 1_000
        + int(match.group(f"{prefix}ms"))
    )
