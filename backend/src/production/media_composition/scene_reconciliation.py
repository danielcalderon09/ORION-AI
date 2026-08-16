"""Pure reconciliation of planned scene timing with durable media durations."""

from __future__ import annotations

from dataclasses import dataclass

from backend.src.production.domain.duration_resolution import (
    DurationResolutionPolicy,
    resolve_audio_first_durations,
)
from backend.src.production.media_composition.exceptions import MediaCompositionPlanError
from backend.src.production.media_composition.ports import (
    CompositionNarrationSource,
    CompositionShotSource,
    CompositionSoundEffectSource,
    CompositionSubtitleSource,
    MediaCompositionSource,
)


@dataclass(frozen=True, slots=True)
class ReconciledSceneTiming:
    scene_id: str
    planned_start_ms: int
    planned_end_ms: int
    actual_start_ms: int
    actual_end_ms: int

    @property
    def shift_ms(self) -> int:
        return self.actual_start_ms - self.planned_start_ms


def reconcile_scene_durations(
    source: MediaCompositionSource,
    *,
    policy: DurationResolutionPolicy,
) -> tuple[MediaCompositionSource, tuple[ReconciledSceneTiming, ...]]:
    """Resolve scene slots from planned timing and natural narration duration."""

    grouped: dict[str, list[CompositionShotSource]] = {}
    order: list[str] = []
    for shot in source.shots:
        if shot.scene_id not in grouped:
            grouped[shot.scene_id] = []
            order.append(shot.scene_id)
        grouped[shot.scene_id].append(shot)

    narration_by_scene = {item.scene_id: item for item in source.narration}
    planned_durations: list[int] = []
    planned_ranges: list[tuple[str, int, int]] = []
    narration_durations: list[int] = []
    for scene_id in order:
        scene_shots = grouped[scene_id]
        planned_start = min(item.shot_start_ms for item in scene_shots)
        planned_end = max(item.shot_end_ms for item in scene_shots)
        planned_duration = planned_end - planned_start
        if planned_duration <= 0:
            raise MediaCompositionPlanError("planned scene duration must be positive")
        planned_ranges.append((scene_id, planned_start, planned_end))
        planned_durations.append(planned_duration)
        narration = narration_by_scene.get(scene_id)
        narration_durations.append(narration.duration_ms if narration is not None else 0)
    resolution = resolve_audio_first_durations(
        requested_target_duration_ms=sum(planned_durations),
        planned_scene_durations_ms=tuple(planned_durations),
        narration_scene_durations_ms=tuple(narration_durations),
        policy=policy,
    )
    resolved_scene_durations = resolution.resolved_scene_durations_ms

    timings: list[ReconciledSceneTiming] = []
    actual_start = 0
    for (scene_id, planned_start, planned_end), resolved_duration in zip(
        planned_ranges,
        resolved_scene_durations,
        strict=True,
    ):
        timings.append(
            ReconciledSceneTiming(
                scene_id=scene_id,
                planned_start_ms=planned_start,
                planned_end_ms=planned_end,
                actual_start_ms=actual_start,
                actual_end_ms=actual_start + resolved_duration,
            )
        )
        actual_start += resolved_duration

    by_scene = {item.scene_id: item for item in timings}
    reconciled_shots = _reconciled_shots(source.shots, grouped, by_scene)
    reconciled_narration = tuple(
        item.model_copy(
            update={
                "timeline_start_ms": by_scene[item.scene_id].actual_start_ms
            }
        )
        for item in source.narration
    )
    sound_effects = tuple(_reconcile_sound_effect(item, by_scene[item.scene_id]) for item in source.sound_effects)
    subtitles = _reconciled_subtitles(source.subtitles, timings, narration_by_scene)
    return (
        source.model_copy(
            update={
                "shots": reconciled_shots,
                "narration": reconciled_narration,
                "sound_effects": sound_effects,
                "subtitles": subtitles,
            }
        ),
        tuple(timings),
    )


def _reconciled_shots(
    source: tuple[CompositionShotSource, ...],
    grouped: dict[str, list[CompositionShotSource]],
    timings: dict[str, ReconciledSceneTiming],
) -> tuple[CompositionShotSource, ...]:
    last_shot_ids = {
        scene_id: max(items, key=lambda item: (item.shot_end_ms, item.shot_number)).shot_id
        for scene_id, items in grouped.items()
    }
    results = []
    for shot in source:
        timing = timings[shot.scene_id]
        planned_duration = timing.planned_end_ms - timing.planned_start_ms
        actual_duration = timing.actual_end_ms - timing.actual_start_ms
        if actual_duration >= planned_duration:
            start = shot.shot_start_ms + timing.shift_ms
            end = shot.shot_end_ms + timing.shift_ms
            if shot.shot_id == last_shot_ids[shot.scene_id]:
                end = timing.actual_end_ms
        else:
            start = timing.actual_start_ms + (
                (shot.shot_start_ms - timing.planned_start_ms) * actual_duration
                // planned_duration
            )
            end = timing.actual_start_ms + (
                (shot.shot_end_ms - timing.planned_start_ms) * actual_duration
                // planned_duration
            )
            if shot.shot_end_ms == timing.planned_end_ms:
                end = timing.actual_end_ms
        results.append(
            shot.model_copy(
                update={
                    "scene_start_ms": timing.actual_start_ms,
                    "shot_start_ms": start,
                    "shot_end_ms": end,
                }
            )
        )
    return tuple(results)


def _reconcile_sound_effect(
    item: CompositionSoundEffectSource,
    timing: ReconciledSceneTiming,
) -> CompositionSoundEffectSource:
    planned_duration = timing.planned_end_ms - timing.planned_start_ms
    actual_duration = timing.actual_end_ms - timing.actual_start_ms
    if actual_duration >= planned_duration:
        return item.model_copy(
            update={"target_offset_ms": item.target_offset_ms + timing.shift_ms}
        )
    relative_offset = max(0, item.target_offset_ms - timing.planned_start_ms)
    target_offset_ms = timing.actual_start_ms + (
        relative_offset * actual_duration // planned_duration
    )
    return item.model_copy(update={"target_offset_ms": target_offset_ms})


def _balanced_scene_durations(
    *,
    narration_durations: tuple[int, ...],
    target_duration_ms: int,
) -> tuple[int, ...]:
    """Spread bounded non-speech time evenly instead of retaining nominal gaps."""

    remaining = target_duration_ms - sum(narration_durations)
    if remaining < 0:
        return narration_durations
    base, remainder = divmod(remaining, len(narration_durations))
    return tuple(
        duration + base + (1 if index < remainder else 0)
        for index, duration in enumerate(narration_durations)
    )


def _reconciled_subtitles(
    source: CompositionSubtitleSource | None,
    timings: list[ReconciledSceneTiming],
    narration_by_scene: dict[str, CompositionNarrationSource],
) -> CompositionSubtitleSource | None:
    if source is None:
        return None
    if len(source.cue_start_ms) == len(timings):
        aligned_starts = tuple(item.actual_start_ms for item in timings)
        aligned_ends = tuple(
            min(
                item.actual_end_ms,
                item.actual_start_ms
                + (
                    narration_by_scene[item.scene_id].duration_ms
                    if item.scene_id in narration_by_scene
                    else source.cue_end_ms[index] - source.cue_start_ms[index]
                ),
            )
            for index, item in enumerate(timings)
        )
        return source.model_copy(
            update={"cue_start_ms": aligned_starts, "cue_end_ms": aligned_ends}
        )
    starts: list[int] = []
    ends: list[int] = []
    last_cue_by_scene: dict[str, int] = {}
    for index, start in enumerate(source.cue_start_ms):
        timing = next(
            (
                item
                for item in timings
                if item.planned_start_ms <= start < item.planned_end_ms
            ),
            timings[-1],
        )
        last_cue_by_scene[timing.scene_id] = index
        adjusted_start = max(timing.actual_start_ms, start + timing.shift_ms)
        starts.append(min(adjusted_start, timing.actual_end_ms - 1))
        ends.append(min(source.cue_end_ms[index] + timing.shift_ms, timing.actual_end_ms))
    for scene_id, index in last_cue_by_scene.items():
        narration = narration_by_scene.get(scene_id)
        timing = next(item for item in timings if item.scene_id == scene_id)
        if narration is not None:
            ends[index] = max(
                ends[index],
                min(timing.actual_end_ms, timing.actual_start_ms + narration.duration_ms),
            )
        ends[index] = min(ends[index], timing.actual_end_ms)
    return source.model_copy(update={"cue_start_ms": tuple(starts), "cue_end_ms": tuple(ends)})


__all__ = ["ReconciledSceneTiming", "reconcile_scene_durations"]
