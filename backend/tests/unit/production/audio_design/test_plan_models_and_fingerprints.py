from __future__ import annotations

import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.audio_design.configuration import (
    AudioDesignConfiguration,
)
from backend.src.production.audio_design.exceptions import AudioDesignPlanError
from backend.src.production.audio_design.fingerprints import (
    audio_design_plan_fingerprint,
    canonical_sha256,
    music_request_fingerprint,
    sound_effect_request_fingerprint,
)
from backend.src.production.audio_design.models import MusicMood
from backend.src.production.audio_design.plan import derive_audio_design_plan

from .conftest import (
    JOB_ID,
    SCRIPT_ARTIFACT_ID,
    SCRIPT_SHA256,
    make_script,
)


def _derive(script):
    return derive_audio_design_plan(
        job_id=JOB_ID,
        source_script_artifact_id=SCRIPT_ARTIFACT_ID,
        source_script_sha256=SCRIPT_SHA256,
        script=script,
        configuration=AudioDesignConfiguration(
            max_music_duration_ms=5_000,
            max_audio_bytes=300_000,
        ),
    )


def test_absent_metadata_produces_valid_zero_asset_plan() -> None:
    plan = _derive(make_script())

    assert plan.music_requirement is None
    assert plan.sound_effect_requirements == ()
    assert plan.total_target_duration_ms == 2_000
    assert plan.plan_fingerprint == audio_design_plan_fingerprint(
        job_id=str(plan.job_id),
        source_script_artifact_id=str(plan.source_script_artifact_id),
        production_script_fingerprint=plan.production_script_fingerprint,
        music_requirement=None,
        sound_effect_requirements=(),
        total_target_duration_ms=2_000,
    )


def test_music_requires_explicit_true_and_uses_controlled_defaults() -> None:
    disabled = _derive(make_script(music={"enabled": False}))
    enabled = _derive(make_script(music={"enabled": True}))

    assert disabled.music_requirement is None
    assert enabled.music_requirement is not None
    assert enabled.music_requirement.mood is MusicMood.NEUTRAL
    assert enabled.music_requirement.target_duration_ms == 2_000
    assert enabled.music_requirement.metadata["copyrighted_style_reference"] is False


def test_explicit_sfx_are_stable_ordered_and_preserve_scene_provenance() -> None:
    script = make_script(
        scene_effects=(
            (
                {"cue_type": "impact", "offset_ms": 500},
                {"cue_type": "transition", "offset_ms": 100},
            ),
            ({"cue_type": "soft_click", "shot_id": "scene-002-shot-001"},),
        )
    )

    first = _derive(script)
    second = _derive(script)

    assert first == second
    assert [item.target_offset_ms for item in first.sound_effect_requirements] == [
        100,
        500,
        1_000,
    ]
    assert first.sound_effect_requirements[-1].shot_id == "scene-002-shot-001"
    assert all(
        item.description == f"Generic {item.cue_type.value} cue"
        for item in first.sound_effect_requirements
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"audio_design": {"music": {"enabled": True, "artist": "someone"}}},
        {"audio_design": {"music": {"enabled": "yes"}}},
        {"audio_design": {"music": {"enabled": True, "mood": "cinematic_artist"}}},
    ],
)
def test_music_metadata_rejects_unknown_or_unsafe_semantics(metadata) -> None:
    script = make_script().model_copy(update={"metadata": metadata})

    with pytest.raises(AudioDesignPlanError):
        _derive(script)


def test_no_implicit_sfx_are_inferred_from_narration_or_visual_text() -> None:
    script = make_script()
    changed = script.model_copy(
        update={
            "scenes": tuple(
                scene.model_copy(
                    update={
                        "narration": "A loud transition and impact happen now.",
                        "visual_intent": "Show a dramatic whoosh.",
                    }
                )
                for scene in script.scenes
            )
        }
    )

    assert _derive(changed).sound_effect_requirements == ()


def test_invalid_sfx_cue_duration_and_shot_fail_closed() -> None:
    invalid_duration = make_script(
        scene_effects=(({"cue_type": "alert", "duration_ms": 60_000},), ())
    )
    invalid_shot = make_script(
        scene_effects=(
            ({"cue_type": "alert", "shot_id": "scene-002-shot-001"},),
            (),
        )
    )

    with pytest.raises(AudioDesignPlanError):
        _derive(invalid_duration)
    with pytest.raises(AudioDesignPlanError):
        _derive(invalid_shot)


def test_request_fingerprints_are_canonical_and_retry_stable(
    explicit_audio_script,
) -> None:
    plan = _derive(explicit_audio_script)
    assert plan.music_requirement is not None
    music = plan.music_requirement
    effect = plan.sound_effect_requirements[0]

    music_first = music_request_fingerprint(
        music,
        sample_rate_hz=24_000,
        channel_count=1,
        sample_width_bytes=2,
    )
    music_retry = music_request_fingerprint(
        music,
        sample_rate_hz=24_000,
        channel_count=1,
        sample_width_bytes=2,
    )
    changed_music = music_request_fingerprint(
        music.model_copy(update={"intensity": music.intensity + 1}),
        sample_rate_hz=24_000,
        channel_count=1,
        sample_width_bytes=2,
    )
    effect_first = sound_effect_request_fingerprint(
        effect,
        sample_rate_hz=24_000,
        channel_count=1,
        sample_width_bytes=2,
    )

    assert music_first == music_retry
    assert changed_music != music_first
    assert effect_first != music_first
    assert "\\" not in music_first and "/" not in music_first


def test_canonical_sha_is_independent_of_mapping_insertion_order() -> None:
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256(json.loads('{"b":2,"a":1}'))


def test_contracts_are_frozen_strict_and_source_bound(explicit_audio_script) -> None:
    plan = _derive(explicit_audio_script)

    with pytest.raises(ValidationError):
        plan.job_id = UUID(int=0)  # type: ignore[misc]
    with pytest.raises(ValidationError):
        type(plan).model_validate({**plan.model_dump(), "unexpected": True})


def test_configuration_rejects_unbounded_music_allocation() -> None:
    with pytest.raises(ValidationError, match="cannot hold"):
        AudioDesignConfiguration(
            max_music_duration_ms=180_000,
            max_audio_bytes=1_024,
        )
