"""Derive minimal explicit audio requirements from the durable script."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from backend.src.production.audio_design.configuration import (
    AudioDesignConfiguration,
)
from backend.src.production.audio_design.duration import (
    DEFAULT_SOUND_EFFECT_DURATION_MS,
    seconds_to_milliseconds,
)
from backend.src.production.audio_design.exceptions import AudioDesignPlanError
from backend.src.production.audio_design.fingerprints import (
    audio_design_plan_fingerprint,
    canonical_sha256,
)
from backend.src.production.audio_design.models import (
    AudioDesignPlan,
    MusicMood,
    MusicRequirement,
    SoundEffectCueType,
    SoundEffectRequirement,
)
from backend.src.production.scripting.models import ProductionScript

_MUSIC_KEYS = {
    "duck_under_narration",
    "enabled",
    "intensity",
    "loopable",
    "mood",
}
_SCENE_AUDIO_KEYS = {"sound_effects"}
_SFX_KEYS = {
    "cue_type",
    "duration_ms",
    "intensity",
    "offset_ms",
    "shot_id",
}


def derive_audio_design_plan(
    *,
    job_id: UUID,
    source_script_artifact_id: UUID,
    source_script_sha256: str,
    script: ProductionScript,
    configuration: AudioDesignConfiguration,
) -> AudioDesignPlan:
    total_duration_ms = seconds_to_milliseconds(script.target_duration_seconds)
    music = _music_requirement(
        script.metadata,
        source_script_sha256=source_script_sha256,
        total_duration_ms=total_duration_ms,
        configuration=configuration,
    )
    effects: list[SoundEffectRequirement] = []
    scene_start_ms = 0
    for scene in script.scenes:
        effects.extend(
            _scene_effects(
                scene.metadata,
                scene_number=scene.scene_number,
                scene_start_ms=scene_start_ms,
                scene_duration_ms=seconds_to_milliseconds(scene.estimated_duration_seconds),
                source_script_sha256=source_script_sha256,
                configuration=configuration,
            )
        )
        scene_start_ms += seconds_to_milliseconds(scene.estimated_duration_seconds)
    ordered_effects = tuple(
        sorted(effects, key=lambda item: (item.target_offset_ms, item.requirement_id))
    )
    fingerprint = audio_design_plan_fingerprint(
        job_id=str(job_id),
        source_script_artifact_id=str(source_script_artifact_id),
        production_script_fingerprint=source_script_sha256,
        music_requirement=music,
        sound_effect_requirements=ordered_effects,
        total_target_duration_ms=total_duration_ms,
    )
    return AudioDesignPlan(
        job_id=job_id,
        source_script_artifact_id=source_script_artifact_id,
        production_script_fingerprint=source_script_sha256,
        music_requirement=music,
        sound_effect_requirements=ordered_effects,
        total_target_duration_ms=total_duration_ms,
        plan_fingerprint=fingerprint,
        metadata={
            "explicit_script_metadata_only": True,
            "implicit_sound_effects": False,
            "simulated": True,
        },
    )


def _music_requirement(
    metadata: Mapping[str, Any],
    *,
    source_script_sha256: str,
    total_duration_ms: int,
    configuration: AudioDesignConfiguration,
) -> MusicRequirement | None:
    audio = _optional_mapping(metadata.get("audio_design"), "script.audio_design")
    if audio is None:
        return None
    _require_exact_keys(audio, {"music"}, "script.audio_design")
    music = _optional_mapping(audio.get("music"), "script.audio_design.music")
    if music is None:
        return None
    _require_exact_keys(music, _MUSIC_KEYS, "script.audio_design.music")
    enabled = music.get("enabled")
    if enabled is False:
        return None
    if enabled is not True:
        raise AudioDesignPlanError("music enabled must be explicitly true or false")
    if not (
        configuration.min_music_duration_ms
        <= total_duration_ms
        <= configuration.max_music_duration_ms
    ):
        raise AudioDesignPlanError("requested music duration is outside safe limits")
    mood = _enum_value(MusicMood, music.get("mood", "neutral"), "music mood")
    intensity = _bounded_int(music.get("intensity", 30), "music intensity", 0, 100)
    loopable = _boolean(music.get("loopable", True), "music loopable")
    ducking = _boolean(
        music.get("duck_under_narration", True),
        "music ducking",
    )
    identity = canonical_sha256(
        {
            "duck_under_narration": ducking,
            "intensity": intensity,
            "loopable": loopable,
            "mood": mood.value,
            "source_script_sha256": source_script_sha256,
            "target_duration_ms": total_duration_ms,
        }
    )
    return MusicRequirement(
        requirement_id=f"music-{identity[:24]}",
        mood=mood,
        intensity=intensity,
        target_duration_ms=total_duration_ms,
        loopable=loopable,
        duck_under_narration=ducking,
        metadata={
            "copyrighted_style_reference": False,
            "explicitly_requested": True,
        },
    )


def _scene_effects(
    metadata: Mapping[str, Any],
    *,
    scene_number: int,
    scene_start_ms: int,
    scene_duration_ms: int,
    source_script_sha256: str,
    configuration: AudioDesignConfiguration,
) -> tuple[SoundEffectRequirement, ...]:
    audio = _optional_mapping(metadata.get("audio_design"), "scene.audio_design")
    if audio is None:
        return ()
    _require_exact_keys(audio, _SCENE_AUDIO_KEYS, "scene.audio_design")
    raw_effects = audio.get("sound_effects")
    if raw_effects is None:
        return ()
    if not isinstance(raw_effects, Sequence) or isinstance(raw_effects, (str, bytes)):
        raise AudioDesignPlanError("scene sound_effects must be an array")
    if len(raw_effects) > 20:
        raise AudioDesignPlanError("scene sound_effects exceed the safe limit")
    scene_id = f"scene-{scene_number:03d}"
    results: list[SoundEffectRequirement] = []
    for index, value in enumerate(raw_effects):
        if not isinstance(value, Mapping):
            raise AudioDesignPlanError("sound-effect cue must be an object")
        _require_exact_keys(value, _SFX_KEYS, "sound-effect cue")
        cue = _enum_value(
            SoundEffectCueType,
            value.get("cue_type"),
            "sound-effect cue type",
        )
        duration = _bounded_int(
            value.get("duration_ms", DEFAULT_SOUND_EFFECT_DURATION_MS[cue]),
            "sound-effect duration",
            configuration.min_sound_effect_duration_ms,
            configuration.max_sound_effect_duration_ms,
        )
        offset = _bounded_int(
            value.get("offset_ms", 0),
            "sound-effect offset",
            0,
            max(0, scene_duration_ms - 1),
        )
        intensity = _bounded_int(
            value.get("intensity", 50),
            "sound-effect intensity",
            0,
            100,
        )
        shot_id = value.get("shot_id")
        if shot_id is not None and (
            not isinstance(shot_id, str) or not shot_id.startswith(f"{scene_id}-shot-")
        ):
            raise AudioDesignPlanError("sound-effect shot ID is invalid")
        identity = canonical_sha256(
            {
                "cue_index": index,
                "cue_type": cue.value,
                "duration_ms": duration,
                "intensity": intensity,
                "offset_ms": offset,
                "scene_id": scene_id,
                "shot_id": shot_id,
                "source_script_sha256": source_script_sha256,
            }
        )
        results.append(
            SoundEffectRequirement(
                requirement_id=f"sfx-{identity[:24]}",
                scene_id=scene_id,
                shot_id=shot_id,
                cue_type=cue,
                description=f"Generic {cue.value} cue",
                target_offset_ms=scene_start_ms + offset,
                target_duration_ms=duration,
                intensity=intensity,
                metadata={
                    "explicitly_requested": True,
                    "realistic_recording": False,
                },
            )
        )
    return tuple(results)


def _optional_mapping(value: Any, path: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise AudioDesignPlanError(f"{path} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    allowed: set[str],
    path: str,
) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise AudioDesignPlanError(f"{path} contains unsupported fields")


def _enum_value(enum_type: type[Any], value: Any, path: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise AudioDesignPlanError(f"{path} is unsupported") from exc


def _bounded_int(value: Any, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AudioDesignPlanError(f"{path} must be an integer")
    if not minimum <= value <= maximum:
        raise AudioDesignPlanError(f"{path} is outside safe limits")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise AudioDesignPlanError(f"{path} must be a boolean")
    return value
