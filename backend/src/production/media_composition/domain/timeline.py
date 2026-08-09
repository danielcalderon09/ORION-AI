"""Pure deterministic construction and validation of a complete timeline."""

from __future__ import annotations

import math
from collections.abc import Mapping
from fractions import Fraction

from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.media_composition.domain.fingerprints import (
    canonical_sha256,
)
from backend.src.production.media_composition.domain.models import (
    CompositionAssetKind,
    CompositionClip,
    CompositionTrack,
    CompositionTrackKind,
    CompositionTransition,
    DuckingInstruction,
    MediaCompositionPlan,
    OutputVideoSpecification,
    SubtitleCue,
    VolumeEnvelope,
    VolumeEnvelopePoint,
)
from backend.src.production.media_composition.exceptions import (
    MediaCompositionPlanError,
)
from backend.src.production.media_composition.ports import MediaCompositionSource
from backend.src.production.media_composition.scene_reconciliation import (
    reconcile_scene_durations,
)

_TRACK_IDS = (
    "track-video",
    "track-narration",
    "track-music",
    "track-sound-effects",
    "track-subtitles",
)


def build_media_composition_plan(
    source: MediaCompositionSource,
    configuration: MediaCompositionConfiguration,
) -> MediaCompositionPlan:
    original_planned_total_ms = source.shots[-1].shot_end_ms
    source, scene_timings = reconcile_scene_durations(source)
    assets = {item.asset_id: item for item in source.assets}
    video_assets = [assets[shot.video_asset_id] for shot in source.shots]
    video_metadata = [_video_metadata(item) for item in video_assets]
    width = _single_value("video width", tuple(item[0] for item in video_metadata))
    height = _single_value("video height", tuple(item[1] for item in video_metadata))
    frame_rate = _single_value("video frame rate", tuple(item[2] for item in video_metadata))
    planned_total_ms = source.shots[-1].shot_end_ms
    narration_total_ms = max(
        item.timeline_start_ms + item.duration_ms for item in source.narration
    )
    total_ms = max(planned_total_ms, narration_total_ms)
    total_frames = _ms_to_frames(total_ms, frame_rate)
    if total_frames <= 0:
        raise MediaCompositionPlanError("timeline duration must be positive")

    video_clips = _video_clips(source, frame_rate, total_ms)
    narration_clips = _narration_clips(source, configuration, frame_rate, total_ms)
    music_clips = _music_clips(
        source,
        configuration,
        frame_rate,
        total_ms,
        allow_timeline_extension=total_ms > original_planned_total_ms,
    )
    sound_effect_clips = _sound_effect_clips(
        source,
        configuration,
        frame_rate,
        total_ms,
    )
    subtitle_cues = _subtitle_cues(source, total_ms)
    subtitle_clips = _subtitle_clips(source, subtitle_cues, frame_rate)

    tracks = (
        CompositionTrack(
            track_id=_TRACK_IDS[0],
            kind=CompositionTrackKind.VIDEO,
            order=0,
            enabled=True,
            clips=video_clips,
        ),
        CompositionTrack(
            track_id=_TRACK_IDS[1],
            kind=CompositionTrackKind.NARRATION,
            order=1,
            enabled=True,
            clips=narration_clips,
        ),
        CompositionTrack(
            track_id=_TRACK_IDS[2],
            kind=CompositionTrackKind.MUSIC,
            order=2,
            enabled=bool(music_clips),
            disabled_reason=None if music_clips else "not_requested",
            clips=music_clips,
        ),
        CompositionTrack(
            track_id=_TRACK_IDS[3],
            kind=CompositionTrackKind.SOUND_EFFECT,
            order=3,
            enabled=bool(sound_effect_clips),
            disabled_reason=None if sound_effect_clips else "no_explicit_cues",
            clips=sound_effect_clips,
        ),
        CompositionTrack(
            track_id=_TRACK_IDS[4],
            kind=CompositionTrackKind.SUBTITLES,
            order=4,
            enabled=bool(subtitle_clips),
            disabled_reason=None if subtitle_clips else "no_durable_subtitle_asset",
            clips=subtitle_clips,
        ),
    )
    transitions = _transitions(source, video_clips, frame_rate)
    ducking = _ducking(source, configuration)
    output = OutputVideoSpecification(
        width=width,
        height=height,
        frame_rate_numerator=frame_rate,
        aspect_ratio=_aspect_ratio(width, height),
        color_space=configuration.color_space,
        title_safe_percent=configuration.title_safe_percent,
        action_safe_percent=configuration.action_safe_percent,
        expected_duration_frames=total_frames,
        expected_duration_ms=_frames_to_ms(total_frames, frame_rate),
    )
    timeline_payload = {
        "ducking": [item.model_dump(mode="json") for item in ducking],
        "output": output.model_dump(mode="json"),
        "subtitle_cues": [item.model_dump(mode="json") for item in subtitle_cues],
        "tracks": [item.model_dump(mode="json") for item in tracks],
        "transitions": [item.model_dump(mode="json") for item in transitions],
    }
    timeline_checksum = canonical_sha256(timeline_payload)
    source_payload = {
        "assets": [item.model_dump(mode="json") for item in source.assets],
        "manifests": [item.model_dump(mode="json") for item in source.source_manifests],
    }
    source_fingerprint = canonical_sha256(source_payload)
    preliminary = MediaCompositionPlan(
        job_id=source.job_id,
        source_fingerprint=source_fingerprint,
        plan_fingerprint="0" * 64,
        timeline_checksum=timeline_checksum,
        source_manifests=source.source_manifests,
        assets=source.assets,
        output=output,
        tracks=tracks,
        transitions=transitions,
        ducking=ducking,
        subtitle_cues=subtitle_cues,
        metadata={
            "content_generation": False,
            "narration_extended_timeline": total_ms > original_planned_total_ms,
            "planned_duration_ms": original_planned_total_ms,
            "reconciled_scene_count": len(scene_timings),
            "video_adaptations": _video_adaptations(video_clips, assets),
            "renderer_neutral": True,
            "subtitle_text_embedded": False,
        },
    )
    fingerprint = canonical_sha256(
        preliminary.model_dump(mode="json", exclude={"plan_fingerprint"})
    )
    return preliminary.model_copy(update={"plan_fingerprint": fingerprint})


def _video_clips(
    source: MediaCompositionSource,
    frame_rate: int,
    total_ms: int,
) -> tuple[CompositionClip, ...]:
    assets = {item.asset_id: item for item in source.assets}
    clips: list[CompositionClip] = []
    previous_end = 0
    for index, shot in enumerate(source.shots):
        asset = assets[shot.video_asset_id]
        start = _ms_to_frames(shot.shot_start_ms, frame_rate)
        timeline_end_ms = total_ms if index == len(source.shots) - 1 else shot.shot_end_ms
        end = _ms_to_frames(timeline_end_ms, frame_rate)
        if start != previous_end:
            raise MediaCompositionPlanError("video timeline contains a gap or overlap")
        if asset.duration_ms is None:
            raise MediaCompositionPlanError("video asset duration is missing")
        source_frames = _ms_to_frames(asset.duration_ms, frame_rate)
        timeline_frames = end - start
        loop_count = max(1, (timeline_frames + source_frames - 1) // source_frames)
        clips.append(
            CompositionClip(
                clip_id=f"clip-video-{shot.shot_id}",
                kind=CompositionAssetKind.VIDEO,
                asset_id=shot.video_asset_id,
                scene_id=shot.scene_id,
                shot_id=shot.shot_id,
                sequence_index=index,
                timeline_start_frame=start,
                timeline_end_frame=end,
                timeline_start_ms=_frames_to_ms(start, frame_rate),
                timeline_end_ms=_frames_to_ms(end, frame_rate),
                source_out_frame=min(source_frames, timeline_frames),
                playback_mode="loop" if loop_count > 1 else "once",
                loop_count=loop_count,
            )
        )
        previous_end = end
    return tuple(clips)


def _narration_clips(
    source: MediaCompositionSource,
    configuration: MediaCompositionConfiguration,
    frame_rate: int,
    total_ms: int,
) -> tuple[CompositionClip, ...]:
    clips = []
    for item in source.narration:
        end_ms = item.timeline_start_ms + item.duration_ms
        if end_ms > total_ms:
            raise MediaCompositionPlanError("narration extends beyond the timeline")
        start = _ms_to_frames(item.timeline_start_ms, frame_rate)
        end = _ms_to_frames(end_ms, frame_rate)
        clips.append(
            CompositionClip(
                clip_id=f"clip-narration-{item.scene_id}",
                kind=CompositionAssetKind.NARRATION,
                asset_id=item.asset_id,
                scene_id=item.scene_id,
                sequence_index=item.sequence_index,
                timeline_start_frame=start,
                timeline_end_frame=end,
                timeline_start_ms=_frames_to_ms(start, frame_rate),
                timeline_end_ms=_frames_to_ms(end, frame_rate),
                volume_envelope=VolumeEnvelope(base_gain_db=configuration.narration_gain_db),
            )
        )
    return tuple(clips)


def _music_clips(
    source: MediaCompositionSource,
    configuration: MediaCompositionConfiguration,
    frame_rate: int,
    total_ms: int,
    *,
    allow_timeline_extension: bool,
) -> tuple[CompositionClip, ...]:
    if source.music is None:
        return ()
    if not allow_timeline_extension and abs(source.music.duration_ms - total_ms) > 1:
        raise MediaCompositionPlanError("music duration does not match timeline")
    end = _ms_to_frames(total_ms, frame_rate)
    source_frames = _ms_to_frames(source.music.duration_ms, frame_rate)
    loop_count = max(1, (end + source_frames - 1) // source_frames)
    fade = min(configuration.fade_duration_ms, total_ms // 2)
    return (
        CompositionClip(
            clip_id=f"clip-music-{source.music.requirement_id}",
            kind=CompositionAssetKind.MUSIC,
            asset_id=source.music.asset_id,
            sequence_index=0,
            timeline_start_frame=0,
            timeline_end_frame=end,
            timeline_start_ms=0,
            timeline_end_ms=_frames_to_ms(end, frame_rate),
            source_out_frame=min(source_frames, end),
            playback_mode="loop" if loop_count > 1 else "once",
            loop_count=loop_count,
            fade_in_ms=fade,
            fade_out_ms=fade,
            volume_envelope=VolumeEnvelope(
                base_gain_db=configuration.music_gain_db,
                points=(
                    VolumeEnvelopePoint(offset_ms=0, gain_db=-60),
                    VolumeEnvelopePoint(
                        offset_ms=fade,
                        gain_db=configuration.music_gain_db,
                    ),
                    VolumeEnvelopePoint(
                        offset_ms=max(fade, total_ms - fade),
                        gain_db=configuration.music_gain_db,
                    ),
                    VolumeEnvelopePoint(offset_ms=total_ms, gain_db=-60),
                )
                if fade
                else (),
            ),
        ),
    )


def _sound_effect_clips(
    source: MediaCompositionSource,
    configuration: MediaCompositionConfiguration,
    frame_rate: int,
    total_ms: int,
) -> tuple[CompositionClip, ...]:
    clips = []
    for index, item in enumerate(source.sound_effects):
        end_ms = item.target_offset_ms + item.duration_ms
        if end_ms > total_ms:
            raise MediaCompositionPlanError("sound effect extends beyond the timeline")
        start = _ms_to_frames(item.target_offset_ms, frame_rate)
        end = _ms_to_frames(end_ms, frame_rate)
        clips.append(
            CompositionClip(
                clip_id=f"clip-sfx-{item.requirement_id}",
                kind=CompositionAssetKind.SOUND_EFFECT,
                asset_id=item.asset_id,
                scene_id=item.scene_id,
                shot_id=item.shot_id,
                sequence_index=index,
                timeline_start_frame=start,
                timeline_end_frame=end,
                timeline_start_ms=_frames_to_ms(start, frame_rate),
                timeline_end_ms=_frames_to_ms(end, frame_rate),
                volume_envelope=VolumeEnvelope(base_gain_db=configuration.sound_effect_gain_db),
            )
        )
    return tuple(clips)


def _subtitle_cues(
    source: MediaCompositionSource,
    total_ms: int,
) -> tuple[SubtitleCue, ...]:
    if source.subtitles is None:
        return ()
    starts = source.subtitles.cue_start_ms
    ends = source.subtitles.cue_end_ms
    hashes = source.subtitles.cue_text_sha256
    if not (len(starts) == len(ends) == len(hashes)):
        raise MediaCompositionPlanError("subtitle cue arrays differ")
    cues = []
    previous_end = 0
    for index, (start, end, digest) in enumerate(zip(starts, ends, hashes, strict=True)):
        if start < previous_end or end > total_ms:
            raise MediaCompositionPlanError("subtitle cues overlap or exceed timeline")
        cues.append(
            SubtitleCue(
                cue_id=f"subtitle-{index + 1:04d}",
                sequence_index=index,
                asset_id=source.subtitles.asset_id,
                source_cue_index=index,
                start_ms=start,
                end_ms=end,
                text_sha256=digest,
            )
        )
        previous_end = end
    return tuple(cues)


def _subtitle_clips(
    source: MediaCompositionSource,
    cues: tuple[SubtitleCue, ...],
    frame_rate: int,
) -> tuple[CompositionClip, ...]:
    return tuple(
        CompositionClip(
            clip_id=f"clip-{cue.cue_id}",
            kind=CompositionAssetKind.SUBTITLES,
            asset_id=cue.asset_id,
            sequence_index=cue.sequence_index,
            timeline_start_frame=_ms_to_frames(cue.start_ms, frame_rate),
            timeline_end_frame=_ms_to_frames(cue.end_ms, frame_rate),
            timeline_start_ms=cue.start_ms,
            timeline_end_ms=cue.end_ms,
        )
        for cue in cues
    )


def _transitions(
    source: MediaCompositionSource,
    clips: tuple[CompositionClip, ...],
    frame_rate: int,
) -> tuple[CompositionTransition, ...]:
    results = []
    for index, (shot, clip) in enumerate(zip(source.shots, clips, strict=True)):
        next_clip = clips[index + 1] if index + 1 < len(clips) else None
        duration_frames = _ms_to_frames(shot.transition_duration_ms, frame_rate)
        if duration_frames > clip.timeline_end_frame - clip.timeline_start_frame:
            raise MediaCompositionPlanError("transition exceeds source clip duration")
        results.append(
            CompositionTransition(
                transition_id=f"transition-{index + 1:04d}",
                sequence_index=index,
                kind=shot.transition_kind,
                boundary_frame=clip.timeline_end_frame,
                duration_frames=duration_frames,
                duration_ms=_frames_to_ms(duration_frames, frame_rate),
                from_clip_id=clip.clip_id,
                to_clip_id=next_clip.clip_id if next_clip else None,
            )
        )
    return tuple(results)


def _ducking(
    source: MediaCompositionSource,
    configuration: MediaCompositionConfiguration,
) -> tuple[DuckingInstruction, ...]:
    if source.music is None or not source.music.duck_under_narration:
        return ()
    return tuple(
        DuckingInstruction(
            instruction_id=f"duck-{index + 1:04d}",
            start_ms=item.timeline_start_ms,
            end_ms=item.timeline_start_ms + item.duration_ms,
            target_gain_db=configuration.music_duck_gain_db,
            attack_ms=min(100, item.duration_ms // 4),
            release_ms=min(200, item.duration_ms // 4),
        )
        for index, item in enumerate(source.narration)
    )


def _video_metadata(asset: object) -> tuple[int, int, int]:
    width = getattr(asset, "width", None)
    height = getattr(asset, "height", None)
    frame_rate = getattr(asset, "frame_rate", None)
    if width is None or height is None or frame_rate is None:
        raise MediaCompositionPlanError("video asset profile is missing")
    return width, height, frame_rate


def _video_adaptations(
    clips: tuple[CompositionClip, ...],
    assets: Mapping[str, object],
) -> dict[str, str]:
    adaptations: dict[str, str] = {}
    for clip in clips:
        asset_duration = getattr(assets[clip.asset_id], "duration_ms", None)
        if not isinstance(asset_duration, int):
            raise MediaCompositionPlanError("video asset duration is missing")
        timeline_duration = clip.timeline_end_ms - clip.timeline_start_ms
        if clip.playback_mode == "loop":
            adaptation = "loop"
        elif asset_duration > timeline_duration:
            adaptation = "trim"
        else:
            adaptation = "none"
        adaptations[clip.clip_id] = adaptation
    return adaptations


def _single_value(name: str, values: tuple[int, ...]) -> int:
    if not values or len(set(values)) != 1:
        raise MediaCompositionPlanError(f"{name} is inconsistent")
    return values[0]


def _ms_to_frames(milliseconds: int, frame_rate: int) -> int:
    return (milliseconds * frame_rate + 500) // 1_000


def _frames_to_ms(frames: int, frame_rate: int) -> int:
    return (frames * 1_000 + frame_rate // 2) // frame_rate


def _aspect_ratio(width: int, height: int) -> str:
    divisor = math.gcd(width, height)
    ratio = Fraction(width // divisor, height // divisor)
    return f"{ratio.numerator}:{ratio.denominator}"
