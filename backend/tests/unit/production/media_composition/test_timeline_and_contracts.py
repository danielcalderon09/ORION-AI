"""Timeline, identity, transition, and serialization behavior."""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.media_composition.domain.fingerprints import canonical_sha256
from backend.src.production.media_composition.domain.models import (
    CompositionAssetAvailability,
    CompositionAssetKind,
    CompositionAssetReference,
    CompositionAssetValidation,
    CompositionTrackKind,
    MediaCompositionPlan,
)
from backend.src.production.media_composition.domain.timeline import (
    build_media_composition_plan,
)
from backend.src.production.media_composition.exceptions import (
    MediaCompositionCorruptError,
    MediaCompositionPlanError,
)
from backend.src.production.media_composition.ports import (
    CompositionNarrationSource,
    CompositionShotSource,
    CompositionSubtitleSource,
    MediaCompositionSource,
)
from backend.src.production.media_composition.serialization import (
    deserialize_media_composition_plan,
    serialize_media_composition_plan,
)


def test_builds_complete_stable_timeline(
    composition_source: MediaCompositionSource,
) -> None:
    first = build_media_composition_plan(
        composition_source,
        MediaCompositionConfiguration(),
    )
    second = build_media_composition_plan(
        composition_source,
        MediaCompositionConfiguration(),
    )

    assert first == second
    assert serialize_media_composition_plan(first) == serialize_media_composition_plan(second)
    assert tuple(track.kind for track in first.tracks) == tuple(CompositionTrackKind)
    assert first.output.expected_duration_frames == 96
    assert first.output.expected_duration_ms == 4_000
    assert [clip.timeline_start_frame for clip in first.tracks[0].clips] == [0, 48]
    assert [clip.timeline_end_frame for clip in first.tracks[0].clips] == [48, 96]
    assert first.tracks[1].clips[0].timeline_end_ms == 4_000
    assert first.tracks[2].clips[0].fade_in_ms == 250
    assert first.tracks[3].clips[0].timeline_start_ms == 1_000
    assert len(first.ducking) == 1
    assert first.tracks[4].enabled is False
    assert first.tracks[4].disabled_reason == "no_durable_subtitle_asset"


def test_relevant_timeline_change_changes_fingerprints(
    composition_source: MediaCompositionSource,
) -> None:
    original = build_media_composition_plan(
        composition_source,
        MediaCompositionConfiguration(),
    )
    changed_shot = composition_source.shots[0].model_copy(
        update={"transition_kind": "dissolve", "transition_duration_ms": 250}
    )
    changed = composition_source.model_copy(
        update={"shots": (changed_shot, composition_source.shots[1])}
    )
    revised = build_media_composition_plan(
        changed,
        MediaCompositionConfiguration(),
    )

    assert revised.timeline_checksum != original.timeline_checksum
    assert revised.plan_fingerprint != original.plan_fingerprint
    assert revised.source_fingerprint == original.source_fingerprint


def test_short_video_uses_freeze_adaptation_without_replay(
    composition_source: MediaCompositionSource,
) -> None:
    first_video = next(
        item for item in composition_source.assets if item.kind is CompositionAssetKind.VIDEO
    )
    short = first_video.model_copy(update={"duration_ms": 1_000, "frame_count": 24})
    assets = tuple(
        sorted(
            (
                short if item.asset_id == first_video.asset_id else item
                for item in composition_source.assets
            ),
            key=lambda item: item.asset_id,
        )
    )
    validations = tuple(
        CompositionAssetValidation(
            asset_id=item.asset_id,
            availability=CompositionAssetAvailability.AVAILABLE,
            relative_path=item.relative_path,
            expected_sha256=item.sha256,
            actual_sha256=item.sha256,
        )
        for item in assets
    )
    plan = build_media_composition_plan(
        composition_source.model_copy(update={"assets": assets, "asset_validation": validations}),
        MediaCompositionConfiguration(),
    )
    clip = plan.tracks[0].clips[0]
    assert clip.playback_mode == "once"
    assert clip.loop_count == 1
    assert clip.source_out_frame == 24
    assert clip.timeline_end_frame == 48
    assert plan.metadata["video_adaptations"][clip.clip_id] == "freeze"


def test_real_narration_extends_timeline_naturally_within_tolerance(
    composition_source: MediaCompositionSource,
) -> None:
    source = _one_scene_real_media_source(composition_source, narration_duration_ms=4_500)

    plan = build_media_composition_plan(source, MediaCompositionConfiguration())

    video = plan.tracks[0].clips[0]
    narration = plan.tracks[1].clips[0]
    assert plan.output.width == 720
    assert plan.output.height == 1_280
    assert plan.output.expected_duration_frames == 108
    assert plan.output.expected_duration_ms == 4_500
    assert plan.metadata["planned_duration_ms"] == 4_000
    assert plan.metadata["requested_target_duration_ms"] == 4_000
    assert plan.metadata["resolved_duration_ms"] == 4_500
    assert plan.metadata["narration_extended_timeline"] is True
    assert plan.metadata["narration_time_stretched"] is False
    assert plan.metadata["target_duration_authoritative"] is False
    assert video.timeline_end_frame == 108
    assert video.playback_mode == "once"
    assert video.loop_count == 1
    assert narration.timeline_end_frame == 108
    assert narration.timeline_end_ms == 4_500
    assert narration.playback_rate == 1.0
    assert plan.subtitle_cues[0].start_ms == 0
    assert plan.subtitle_cues[0].end_ms == 4_500


def test_narration_within_video_keeps_planned_timeline(
    composition_source: MediaCompositionSource,
) -> None:
    source = _one_scene_real_media_source(composition_source, narration_duration_ms=3_500)

    plan = build_media_composition_plan(source, MediaCompositionConfiguration())

    assert plan.output.expected_duration_frames == 96
    assert plan.output.expected_duration_ms == 4_000
    assert plan.metadata["narration_extended_timeline"] is False
    assert plan.tracks[0].clips[0].playback_mode == "once"
    assert plan.tracks[1].clips[0].timeline_end_ms == 3_500


def test_two_real_scenes_resolve_audio_first_without_video_repetition(
    composition_source: MediaCompositionSource,
) -> None:
    source = _two_scene_real_media_source(composition_source)

    plan = build_media_composition_plan(source, MediaCompositionConfiguration())

    videos = plan.tracks[0].clips
    narration = plan.tracks[1].clips
    assert plan.output.expected_duration_ms == 9_250
    assert plan.output.expected_duration_frames == 222
    assert [(item.timeline_start_ms, item.timeline_end_ms) for item in videos] == [
        (0, 4_250),
        (4_250, 9_250),
    ]
    assert len({item.asset_id for item in videos}) == 2
    assert all(item.playback_mode == "once" and item.loop_count == 1 for item in videos)
    assert [(item.timeline_start_ms, item.timeline_end_ms) for item in narration] == [
        (0, 4_250),
        (4_250, 9_250),
    ]
    assert [item.playback_rate for item in narration] == [1.0, 1.0]
    assert [(item.start_ms, item.end_ms) for item in plan.subtitle_cues] == [
        (0, 4_250),
        (4_250, 9_250),
    ]
    assert plan.metadata["narration_extended_timeline"] is True
    assert plan.metadata["requested_target_duration_ms"] == 8_000
    assert plan.metadata["resolved_duration_ms"] == 9_250
    assert plan.metadata["target_duration_authoritative"] is False
    assert plan.metadata["video_adaptations"] == {
        "clip-video-scene-001-shot-001": "freeze",
        "clip-video-scene-002-shot-001": "freeze",
    }


def test_future_six_second_clips_are_trimmed_to_audio_first_slots(
    composition_source: MediaCompositionSource,
) -> None:
    source = _two_scene_real_media_source(
        composition_source,
        video_durations_ms=(6_000, 6_000),
    )

    plan = build_media_composition_plan(source, MediaCompositionConfiguration())

    assert plan.output.expected_duration_ms == 9_250
    assert plan.metadata["video_adaptations"] == {
        "clip-video-scene-001-shot-001": "trim",
        "clip-video-scene-002-shot-001": "trim",
    }


def test_narration_within_planned_scenes_keeps_requested_target(
    composition_source: MediaCompositionSource,
) -> None:
    source = _two_scene_real_media_source(
        composition_source,
        narration_durations_ms=(3_500, 3_800),
    )

    plan = build_media_composition_plan(source, MediaCompositionConfiguration())

    assert plan.output.expected_duration_ms == 8_000
    assert [(item.timeline_start_ms, item.timeline_end_ms) for item in plan.tracks[0].clips] == [
        (0, 4_000),
        (4_000, 8_000),
    ]
    assert [item.playback_rate for item in plan.tracks[1].clips] == [1.0, 1.0]


def _two_scene_real_media_source(
    source: MediaCompositionSource,
    *,
    narration_durations_ms: tuple[int, int] = (4_250, 5_000),
    video_durations_ms: tuple[int, int] = (4_000, 4_000),
) -> MediaCompositionSource:
    video_templates = tuple(
        item for item in source.assets if item.kind is CompositionAssetKind.VIDEO
    )
    narration_template = next(
        item for item in source.assets if item.kind is CompositionAssetKind.NARRATION
    )
    videos = tuple(
        template.model_copy(
            update={
                "duration_ms": video_durations_ms[index - 1],
                "frame_count": video_durations_ms[index - 1] * 24 // 1_000,
                "width": 720,
                "height": 1_280,
                "scene_id": f"scene-{index:03d}",
                "shot_id": f"scene-{index:03d}-shot-001",
            }
        )
        for index, template in enumerate(video_templates, start=1)
    )
    narration_assets = tuple(
        narration_template.model_copy(
            update={
                "asset_id": f"speech-real-scene-{index:03d}",
                "artifact_id": UUID(f"30000000-0000-4000-8004-{index:012d}"),
                "relative_path": f"production/test/speech-real-{index}.wav",
                "duration_ms": duration,
                "frame_count": duration * 24,
                "scene_id": f"scene-{index:03d}",
            }
        )
        for index, duration in enumerate(narration_durations_ms, start=1)
    )
    subtitle = CompositionAssetReference(
        asset_id="subtitles-real-two-scenes",
        artifact_id="50000000-0000-4000-8000-000000000903",
        kind=CompositionAssetKind.SUBTITLES,
        relative_path="production/test/subtitles-real-two-scenes.srt",
        mime_type="application/x-subrip",
        sha256="1" * 64,
        fingerprint="2" * 64,
        size_bytes=225,
    )
    assets = tuple(
        sorted((*videos, *narration_assets, subtitle), key=lambda item: item.asset_id)
    )
    validations = tuple(
        CompositionAssetValidation(
            asset_id=item.asset_id,
            availability=CompositionAssetAvailability.AVAILABLE,
            relative_path=item.relative_path,
            expected_sha256=item.sha256,
            actual_sha256=item.sha256,
        )
        for item in assets
    )
    return source.model_copy(
        update={
            "assets": assets,
            "asset_validation": validations,
            "shots": tuple(
                CompositionShotSource(
                    scene_id=f"scene-{index:03d}",
                    shot_id=f"scene-{index:03d}-shot-001",
                    scene_number=index,
                    shot_number=1,
                    scene_start_ms=(index - 1) * 4_000,
                    shot_start_ms=(index - 1) * 4_000,
                    shot_end_ms=index * 4_000,
                    transition_kind="none",
                    transition_duration_ms=0,
                    video_asset_id=videos[index - 1].asset_id,
                )
                for index in (1, 2)
            ),
            "narration": tuple(
                CompositionNarrationSource(
                    scene_id=f"scene-{index:03d}",
                    sequence_index=index - 1,
                    timeline_start_ms=(index - 1) * 4_000,
                    duration_ms=duration,
                    asset_id=narration_assets[index - 1].asset_id,
                )
                for index, duration in enumerate(narration_durations_ms, start=1)
            ),
            "music": None,
            "sound_effects": (),
            "subtitles": CompositionSubtitleSource(
                asset_id=subtitle.asset_id,
                cue_start_ms=(0, narration_durations_ms[0]),
                cue_end_ms=(
                    narration_durations_ms[0],
                    sum(narration_durations_ms),
                ),
                cue_text_sha256=("3" * 64, "4" * 64),
            ),
        }
    )


def test_three_scene_real_narration_shifts_following_tracks_without_overlap(
    composition_source: MediaCompositionSource,
) -> None:
    video_template = next(
        item for item in composition_source.assets if item.kind is CompositionAssetKind.VIDEO
    )
    narration_template = next(
        item
        for item in composition_source.assets
        if item.kind is CompositionAssetKind.NARRATION
    )
    music = next(
        item for item in composition_source.assets if item.kind is CompositionAssetKind.MUSIC
    ).model_copy(update={"duration_ms": 15_000, "frame_count": 360_000})
    videos = tuple(
        video_template.model_copy(
            update={
                "asset_id": f"video-scene-{index:03d}",
                "artifact_id": UUID(f"30000000-0000-4000-8001-{index:012d}"),
                "relative_path": f"production/test/video-{index}.mp4",
                "duration_ms": duration,
                "frame_count": duration * 24 // 1_000,
                "scene_id": f"scene-{index:03d}",
                "shot_id": f"scene-{index:03d}-shot-001",
            }
        )
        for index, duration in enumerate((4_000, 6_000, 6_000), start=1)
    )
    narrations = tuple(
        narration_template.model_copy(
            update={
                "asset_id": f"speech-scene-{index:03d}",
                "artifact_id": UUID(f"30000000-0000-4000-8002-{index:012d}"),
                "relative_path": f"production/test/speech-{index}.wav",
                "duration_ms": duration,
                "frame_count": duration * 24,
                "scene_id": f"scene-{index:03d}",
            }
        )
        for index, duration in enumerate((5_000, 4_000, 6_000), start=1)
    )
    subtitle_asset = narration_template.model_copy(
        update={
            "asset_id": "subtitles-three-scenes",
            "artifact_id": UUID("30000000-0000-4000-8003-000000000001"),
            "kind": CompositionAssetKind.SUBTITLES,
            "relative_path": "production/test/subtitles.srt",
            "mime_type": "application/x-subrip",
            "duration_ms": None,
            "sample_rate_hz": None,
            "channel_count": None,
            "sample_width_bytes": None,
            "frame_count": None,
            "scene_id": None,
        }
    )
    assets = tuple(
        sorted((*videos, *narrations, music, subtitle_asset), key=lambda item: item.asset_id)
    )
    validations = tuple(
        CompositionAssetValidation(
            asset_id=item.asset_id,
            availability=CompositionAssetAvailability.AVAILABLE,
            relative_path=item.relative_path,
            expected_sha256=item.sha256,
            actual_sha256=item.sha256,
        )
        for item in assets
    )
    planned = ((0, 4_000), (4_000, 9_000), (9_000, 15_000))
    shots = tuple(
        CompositionShotSource(
            scene_id=f"scene-{index:03d}",
            shot_id=f"scene-{index:03d}-shot-001",
            scene_number=index,
            shot_number=1,
            scene_start_ms=start,
            shot_start_ms=start,
            shot_end_ms=end,
            transition_kind="none",
            transition_duration_ms=0,
            video_asset_id=videos[index - 1].asset_id,
        )
        for index, (start, end) in enumerate(planned, start=1)
    )
    narration_sources = tuple(
        CompositionNarrationSource(
            scene_id=f"scene-{index:03d}",
            sequence_index=index - 1,
            timeline_start_ms=planned[index - 1][0],
            duration_ms=duration,
            asset_id=narrations[index - 1].asset_id,
        )
        for index, duration in enumerate((5_000, 4_000, 6_000), start=1)
    )
    subtitles = CompositionSubtitleSource(
        asset_id="subtitles-three-scenes",
        cue_start_ms=(0, 4_000, 9_000),
        cue_end_ms=(4_000, 9_000, 15_000),
        cue_text_sha256=("a" * 64, "b" * 64, "c" * 64),
    )
    source = composition_source.model_copy(
        update={
            "assets": assets,
            "asset_validation": validations,
            "shots": shots,
            "narration": narration_sources,
            "music": composition_source.music.model_copy(update={"duration_ms": 15_000}),
            "sound_effects": (),
            "subtitles": subtitles,
        }
    )

    plan = build_media_composition_plan(source, MediaCompositionConfiguration())

    video_clips = plan.tracks[0].clips
    assert [(item.timeline_start_ms, item.timeline_end_ms) for item in video_clips] == [
        (0, 5_000),
        (5_000, 10_000),
        (10_000, 16_000),
    ]
    assert [(item.timeline_start_ms, item.timeline_end_ms) for item in plan.tracks[1].clips] == [
        (0, 5_000),
        (5_000, 9_000),
        (10_000, 16_000),
    ]
    assert [(item.start_ms, item.end_ms) for item in plan.subtitle_cues] == [
        (0, 5_000),
        (5_000, 9_000),
        (10_000, 16_000),
    ]
    assert plan.output.expected_duration_ms == 16_000
    assert video_clips[0].playback_mode == "once"
    assert video_clips[1].playback_mode == "once"
    assert plan.metadata["video_adaptations"] == {
        "clip-video-scene-001-shot-001": "freeze",
        "clip-video-scene-002-shot-001": "trim",
        "clip-video-scene-003-shot-001": "none",
    }


def test_existing_subtitles_are_aligned_without_copying_text(
    composition_source: MediaCompositionSource,
) -> None:
    subtitle = CompositionAssetReference(
        asset_id="subtitles-aaaaaaaaaaaaaaaaaaaaaaaa",
        artifact_id="50000000-0000-4000-8000-000000000901",
        kind=CompositionAssetKind.SUBTITLES,
        relative_path=(
            f"production/{composition_source.job_id}/generating_subtitles/attempt-1/subtitles.srt"
        ),
        mime_type="application/x-subrip",
        sha256="a" * 64,
        fingerprint="b" * 64,
        size_bytes=100,
    )
    assets = tuple(sorted((*composition_source.assets, subtitle), key=lambda item: item.asset_id))
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
    source = composition_source.model_copy(
        update={
            "assets": assets,
            "asset_validation": validation,
            "subtitles": CompositionSubtitleSource(
                asset_id=subtitle.asset_id,
                cue_start_ms=(0, 2_000),
                cue_end_ms=(1_500, 3_500),
                cue_text_sha256=("c" * 64, "d" * 64),
            ),
        }
    )
    plan = build_media_composition_plan(source, MediaCompositionConfiguration())

    assert plan.tracks[4].enabled
    assert len(plan.subtitle_cues) == 2
    assert plan.subtitle_cues[0].placement == "bottom_center"
    assert "text" not in plan.subtitle_cues[0].model_dump()


def _one_scene_real_media_source(
    source: MediaCompositionSource,
    *,
    narration_duration_ms: int,
) -> MediaCompositionSource:
    video = next(item for item in source.assets if item.kind is CompositionAssetKind.VIDEO)
    narration = next(
        item for item in source.assets if item.kind is CompositionAssetKind.NARRATION
    )
    real_video = video.model_copy(
        update={
            "duration_ms": 4_000,
            "frame_count": 96,
            "height": 1_280,
            "width": 720,
        }
    )
    real_narration = narration.model_copy(
        update={
            "duration_ms": narration_duration_ms,
            "frame_count": narration_duration_ms * 24,
        }
    )
    subtitle = CompositionAssetReference(
        asset_id="subtitles-real-short-aaaaaaaaaaaa",
        artifact_id="50000000-0000-4000-8000-000000000902",
        kind=CompositionAssetKind.SUBTITLES,
        relative_path=(f"production/{source.job_id}/generating_subtitles/attempt-1/subtitles.srt"),
        mime_type="application/x-subrip",
        sha256="e" * 64,
        fingerprint="f" * 64,
        size_bytes=134,
    )
    assets = tuple(sorted((real_video, real_narration, subtitle), key=lambda item: item.asset_id))
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
    return source.model_copy(
        update={
            "asset_validation": validation,
            "assets": assets,
            "music": None,
            "narration": (
                CompositionNarrationSource(
                    scene_id="scene-001",
                    sequence_index=0,
                    timeline_start_ms=0,
                    duration_ms=narration_duration_ms,
                    asset_id=real_narration.asset_id,
                ),
            ),
            "shots": (
                CompositionShotSource(
                    scene_id="scene-001",
                    shot_id="scene-001-shot-001",
                    scene_number=1,
                    shot_number=1,
                    scene_start_ms=0,
                    shot_start_ms=0,
                    shot_end_ms=4_000,
                    transition_kind="none",
                    transition_duration_ms=0,
                    video_asset_id=real_video.asset_id,
                ),
            ),
            "sound_effects": (),
            "subtitles": CompositionSubtitleSource(
                asset_id=subtitle.asset_id,
                cue_start_ms=(0,),
                cue_end_ms=(4_000,),
                cue_text_sha256=("a" * 64,),
            ),
        }
    )


def test_gap_and_duration_mismatch_fail_closed(
    composition_source: MediaCompositionSource,
) -> None:
    gap_shot = composition_source.shots[1].model_copy(update={"shot_start_ms": 2_100})
    with pytest.raises(MediaCompositionPlanError, match="gap or overlap"):
        build_media_composition_plan(
            composition_source.model_copy(
                update={"shots": (composition_source.shots[0], gap_shot)}
            ),
            MediaCompositionConfiguration(),
        )

    assert composition_source.music is not None
    short_music = composition_source.music.model_copy(update={"duration_ms": 3_998})
    with pytest.raises(MediaCompositionPlanError, match="music duration"):
        build_media_composition_plan(
            composition_source.model_copy(update={"music": short_music}),
            MediaCompositionConfiguration(),
        )


def test_plan_is_immutable_strict_and_round_trips(
    composition_source: MediaCompositionSource,
) -> None:
    plan = build_media_composition_plan(
        composition_source,
        MediaCompositionConfiguration(),
    )
    content = serialize_media_composition_plan(plan)
    assert content.endswith(b"\n")
    assert deserialize_media_composition_plan(content) == plan

    with pytest.raises(ValidationError):
        MediaCompositionPlan.model_validate({**plan.model_dump(mode="json"), "unexpected": True})
    with pytest.raises(ValidationError):
        plan.output.width = 1920  # type: ignore[misc]
    clip_payload = plan.tracks[1].clips[0].model_dump(mode="json")
    with pytest.raises(ValidationError):
        type(plan.tracks[1].clips[0]).model_validate(
            {**clip_payload, "playback_rate": 1.25}
        )


def test_historical_plan_without_playback_rate_keeps_its_durable_identity(
    composition_source: MediaCompositionSource,
) -> None:
    plan = build_media_composition_plan(
        composition_source,
        MediaCompositionConfiguration(),
    )
    payload = plan.model_dump(mode="json")
    for track in payload["tracks"]:
        for clip in track["clips"]:
            clip.pop("playback_rate")
    timeline_payload = {
        "ducking": payload["ducking"],
        "output": payload["output"],
        "subtitle_cues": payload["subtitle_cues"],
        "tracks": payload["tracks"],
        "transitions": payload["transitions"],
    }
    payload["timeline_checksum"] = canonical_sha256(timeline_payload)
    payload["plan_fingerprint"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "plan_fingerprint"}
    )
    content = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()

    restored = deserialize_media_composition_plan(content)

    assert restored.plan_fingerprint == payload["plan_fingerprint"]
    assert all(clip.playback_rate == 1.0 for track in restored.tracks for clip in track.clips)


def test_strict_json_and_identity_drift_are_rejected(
    composition_source: MediaCompositionSource,
) -> None:
    plan = build_media_composition_plan(
        composition_source,
        MediaCompositionConfiguration(),
    )
    payload = plan.model_dump(mode="json")
    duplicate = b'{"schema_version":"1.0.0","schema_version":"1.0.0"}'
    with pytest.raises(MediaCompositionCorruptError):
        deserialize_media_composition_plan(duplicate)

    payload["output"]["width"] = 1920
    drifted = (
        json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    with pytest.raises(MediaCompositionCorruptError, match="timeline checksum"):
        deserialize_media_composition_plan(drifted)

    with pytest.raises(MediaCompositionCorruptError):
        deserialize_media_composition_plan(b'{"value":NaN}')
