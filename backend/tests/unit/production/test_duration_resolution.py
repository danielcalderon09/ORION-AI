"""Audio-first duration tolerance and future pre-video safety."""

from decimal import Decimal

from backend.src.production.domain.duration_resolution import (
    DurationResolutionPolicy,
    durable_duration_resolution,
    resolve_audio_first_durations,
)

POLICY = DurationResolutionPolicy(
    maximum_absolute_extension_ms=3_000,
    maximum_relative_extension_ratio=Decimal("0.20"),
)


def test_target_is_guidance_for_short_and_long_measured_narration() -> None:
    for measured in (13_000, 16_000, 20_000):
        resolution = resolve_audio_first_durations(
            requested_target_duration_ms=15_000,
            planned_scene_durations_ms=(15_000,),
            narration_scene_durations_ms=(measured,),
            policy=POLICY,
        )
        assert resolution.resolved_duration_ms == max(15_000, measured)


def test_multi_scene_resolution_preserves_order_and_audio_coverage() -> None:
    resolution = resolve_audio_first_durations(
        requested_target_duration_ms=15_000,
        planned_scene_durations_ms=(5_000, 5_000, 5_000),
        narration_scene_durations_ms=(5_500, 7_000, 4_500),
        policy=POLICY,
    )

    assert resolution.resolved_scene_durations_ms == (5_500, 7_000, 5_000)
    assert resolution.resolved_duration_ms == 17_500


def test_natural_narration_within_configured_tolerance_is_accepted() -> None:
    resolution = resolve_audio_first_durations(
        requested_target_duration_ms=30_000,
        planned_scene_durations_ms=(15_000, 15_000),
        narration_scene_durations_ms=(16_000, 16_000),
        policy=POLICY,
    )

    assert resolution.resolved_scene_durations_ms == (16_000, 16_000)
    assert resolution.resolved_duration_ms == 32_000
    assert resolution.maximum_allowed_duration_ms == 33_000


def test_dramatic_extension_is_accepted_as_measured_timeline() -> None:
    resolution = resolve_audio_first_durations(
        requested_target_duration_ms=30_000,
        planned_scene_durations_ms=(15_000, 15_000),
        narration_scene_durations_ms=(24_000, 24_000),
        policy=POLICY,
    )

    assert resolution.resolved_duration_ms == 48_000


def test_eight_second_reference_job_is_within_relative_limit() -> None:
    resolution = resolve_audio_first_durations(
        requested_target_duration_ms=8_000,
        planned_scene_durations_ms=(4_000, 4_000),
        narration_scene_durations_ms=(4_250, 5_000),
        policy=POLICY,
    )

    assert resolution.resolved_scene_durations_ms == (4_250, 5_000)
    assert resolution.resolved_duration_ms == 9_250
    assert resolution.maximum_allowed_duration_ms == 9_600

    durable = durable_duration_resolution(
        scene_ids=("scene-001", "scene-002"),
        planned_scene_durations_ms=(4_000, 4_000),
        narration_scene_durations_ms=(4_250, 5_000),
        resolution=resolution,
    )
    assert durable.accepted
    assert tuple(scene.resolved_duration_ms for scene in durable.scenes) == (4_250, 5_000)


def test_excessive_resolution_exposes_safe_durable_values() -> None:
    resolution = resolve_audio_first_durations(
        requested_target_duration_ms=8_000,
        planned_scene_durations_ms=(4_000, 4_000),
        narration_scene_durations_ms=(6_000, 6_000),
        policy=POLICY,
    )

    assert resolution.resolved_duration_ms == 12_000
    assert resolution.maximum_allowed_duration_ms == 9_600
    durable = durable_duration_resolution(
        scene_ids=("scene-001", "scene-002"),
        planned_scene_durations_ms=(4_000, 4_000),
        narration_scene_durations_ms=(6_000, 6_000),
        resolution=resolution,
    )
    assert durable.accepted
