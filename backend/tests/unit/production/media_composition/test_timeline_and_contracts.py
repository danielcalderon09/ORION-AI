"""Timeline, identity, transition, and serialization behavior."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
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


def test_short_video_is_explicitly_looped_without_changing_timeline(
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
    assert clip.playback_mode == "loop"
    assert clip.loop_count == 2
    assert clip.source_out_frame == 24
    assert clip.timeline_end_frame == 48


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
