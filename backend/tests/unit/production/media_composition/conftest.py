"""Deterministic media-composition fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.media_composition.domain.fingerprints import (
    canonical_sha256,
)
from backend.src.production.media_composition.domain.models import (
    CompositionAssetAvailability,
    CompositionAssetKind,
    CompositionAssetReference,
    CompositionAssetValidation,
    CompositionTransitionKind,
    SourceManifestReference,
)
from backend.src.production.media_composition.ports import (
    CompositionMusicSource,
    CompositionNarrationSource,
    CompositionShotSource,
    CompositionSoundEffectSource,
    MediaCompositionSource,
)
from backend.src.production.runtime.context import StageContext

NOW = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000901")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000901")


def _asset(
    *,
    asset_id: str,
    artifact_id: str,
    kind: CompositionAssetKind,
    path: str,
    duration_ms: int,
    scene_id: str | None = None,
    shot_id: str | None = None,
) -> CompositionAssetReference:
    digest = canonical_sha256({"asset_id": asset_id})
    common = {
        "asset_id": asset_id,
        "artifact_id": UUID(artifact_id),
        "kind": kind,
        "relative_path": path,
        "mime_type": "video/mp4" if kind is CompositionAssetKind.VIDEO else "audio/wav",
        "sha256": digest,
        "fingerprint": canonical_sha256({"fingerprint": asset_id}),
        "size_bytes": 1_024,
        "duration_ms": duration_ms,
        "scene_id": scene_id,
        "shot_id": shot_id,
    }
    if kind is CompositionAssetKind.VIDEO:
        return CompositionAssetReference(
            **common,
            width=1280,
            height=720,
            frame_rate=24,
            frame_count=(duration_ms * 24 + 500) // 1_000,
        )
    return CompositionAssetReference(
        **common,
        sample_rate_hz=24_000,
        channel_count=1,
        sample_width_bytes=2,
        frame_count=duration_ms * 24,
    )


def make_source() -> MediaCompositionSource:
    video_1 = _asset(
        asset_id="video-shot-001",
        artifact_id="30000000-0000-4000-8000-000000000901",
        kind=CompositionAssetKind.VIDEO,
        path=f"production/{JOB_ID}/assets/video-clips/video-shot-001.mp4",
        duration_ms=2_000,
        scene_id="scene-001",
        shot_id="scene-001-shot-001",
    )
    video_2 = _asset(
        asset_id="video-shot-002",
        artifact_id="30000000-0000-4000-8000-000000000902",
        kind=CompositionAssetKind.VIDEO,
        path=f"production/{JOB_ID}/assets/video-clips/video-shot-002.mp4",
        duration_ms=2_000,
        scene_id="scene-001",
        shot_id="scene-001-shot-002",
    )
    narration = _asset(
        asset_id="speech-scene-001",
        artifact_id="30000000-0000-4000-8000-000000000903",
        kind=CompositionAssetKind.NARRATION,
        path=f"production/{JOB_ID}/assets/speech/speech-scene-001.wav",
        duration_ms=4_000,
        scene_id="scene-001",
    )
    music = _asset(
        asset_id="audio-music-aaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb",
        artifact_id="30000000-0000-4000-8000-000000000904",
        kind=CompositionAssetKind.MUSIC,
        path=f"production/{JOB_ID}/assets/music/music.wav",
        duration_ms=4_000,
    )
    sound_effect = _asset(
        asset_id="audio-sfx-aaaaaaaaaaaaaaaaaaaaaaaa-cccccccccccccccc",
        artifact_id="30000000-0000-4000-8000-000000000905",
        kind=CompositionAssetKind.SOUND_EFFECT,
        path=f"production/{JOB_ID}/assets/sound-effects/effect.wav",
        duration_ms=250,
        scene_id="scene-001",
        shot_id="scene-001-shot-001",
    )
    assets = tuple(
        sorted(
            (video_1, video_2, narration, music, sound_effect),
            key=lambda item: item.asset_id,
        )
    )
    validation = tuple(
        CompositionAssetValidation(
            asset_id=item.asset_id,
            availability=CompositionAssetAvailability.AVAILABLE,
            relative_path=item.relative_path,
            expected_sha256=item.sha256,
            actual_sha256=item.sha256,
        )
        for item in assets
    )
    manifests = tuple(
        sorted(
            (
                SourceManifestReference(
                    artifact_id=UUID(f"40000000-0000-4000-8000-{index:012d}"),
                    artifact_type=artifact_type,
                    relative_path=(
                        f"production/{JOB_ID}/{artifact_type.value}/attempt-1/source.json"
                    ),
                    schema_version="1.0.0",
                    sha256=f"{index:x}" * 64,
                    size_bytes=1_024,
                )
                for index, artifact_type in enumerate(
                    (
                        ArtifactType.PRODUCTION_SCRIPT,
                        ArtifactType.PRODUCTION_SCENE_PLAN,
                        ArtifactType.PRODUCTION_VIDEO_CLIP_MANIFEST,
                        ArtifactType.PRODUCTION_SPEECH_GENERATION_MANIFEST,
                        ArtifactType.PRODUCTION_AUDIO_DESIGN_MANIFEST,
                    ),
                    start=1,
                )
            ),
            key=lambda item: item.artifact_type.value,
        )
    )
    return MediaCompositionSource(
        job_id=JOB_ID,
        source_manifests=manifests,
        assets=assets,
        asset_validation=validation,
        shots=(
            CompositionShotSource(
                scene_id="scene-001",
                shot_id="scene-001-shot-001",
                scene_number=1,
                shot_number=1,
                scene_start_ms=0,
                shot_start_ms=0,
                shot_end_ms=2_000,
                transition_kind=CompositionTransitionKind.CUT,
                transition_duration_ms=0,
                video_asset_id=video_1.asset_id,
            ),
            CompositionShotSource(
                scene_id="scene-001",
                shot_id="scene-001-shot-002",
                scene_number=1,
                shot_number=2,
                scene_start_ms=0,
                shot_start_ms=2_000,
                shot_end_ms=4_000,
                transition_kind=CompositionTransitionKind.NONE,
                transition_duration_ms=0,
                video_asset_id=video_2.asset_id,
            ),
        ),
        narration=(
            CompositionNarrationSource(
                scene_id="scene-001",
                sequence_index=0,
                timeline_start_ms=0,
                duration_ms=4_000,
                asset_id=narration.asset_id,
            ),
        ),
        music=CompositionMusicSource(
            requirement_id="music-aaaaaaaaaaaaaaaaaaaaaaaa",
            duration_ms=4_000,
            duck_under_narration=True,
            asset_id=music.asset_id,
        ),
        sound_effects=(
            CompositionSoundEffectSource(
                requirement_id="sfx-aaaaaaaaaaaaaaaaaaaaaaaa",
                scene_id="scene-001",
                shot_id="scene-001-shot-001",
                target_offset_ms=1_000,
                duration_ms=250,
                asset_id=sound_effect.asset_id,
            ),
        ),
    )


def make_command_context() -> tuple[StageCommand, StageContext]:
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.BUILDING_TIMELINE,
        attempt_number=1,
        idempotency_key="media-composition:test",
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.BUILDING_TIMELINE,
        attempt_number=1,
        workspace_relative_path=(f"production/{JOB_ID}/building_timeline/attempt-1"),
        correlation_id=JOB_ID,
    )
    return command, context


@pytest.fixture
def composition_source() -> MediaCompositionSource:
    return make_source()


@dataclass
class StaticSourceReader:
    source: MediaCompositionSource

    async def read(self, *, context: object) -> MediaCompositionSource:
        del context
        return self.source
