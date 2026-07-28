"""Canonical SHA-256 identities for audio-design plans and requests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.src.production.audio_design.models import (
    AUDIO_DESIGN_SCHEMA_VERSION,
    AudioDesignPlan,
    MusicRequirement,
    SoundEffectRequirement,
)

SIMULATED_MUSIC_PROVIDER_ID = "orion-simulated-music"
SIMULATED_SOUND_EFFECT_PROVIDER_ID = "orion-simulated-sound-effects"
SIMULATED_MUSIC_PROVIDER_VERSION = "simulated-music-v1"
SIMULATED_SOUND_EFFECT_PROVIDER_VERSION = "simulated-sfx-v1"


def canonical_sha256(value: dict[str, Any] | list[Any]) -> str:
    content = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def music_request_fingerprint(
    requirement: MusicRequirement,
    *,
    sample_rate_hz: int,
    channel_count: int,
    sample_width_bytes: int,
) -> str:
    return canonical_sha256(
        {
            "audio_format": "wav_pcm",
            "channel_count": channel_count,
            "duration_ms": requirement.target_duration_ms,
            "intensity": requirement.intensity,
            "loopable": requirement.loopable,
            "mood": requirement.mood.value,
            "provider_id": SIMULATED_MUSIC_PROVIDER_ID,
            "provider_version": SIMULATED_MUSIC_PROVIDER_VERSION,
            "requirement_id": requirement.requirement_id,
            "sample_rate_hz": sample_rate_hz,
            "sample_width_bytes": sample_width_bytes,
            "schema_version": AUDIO_DESIGN_SCHEMA_VERSION,
        }
    )


def sound_effect_request_fingerprint(
    requirement: SoundEffectRequirement,
    *,
    sample_rate_hz: int,
    channel_count: int,
    sample_width_bytes: int,
) -> str:
    return canonical_sha256(
        {
            "audio_format": "wav_pcm",
            "channel_count": channel_count,
            "cue_type": requirement.cue_type.value,
            "duration_ms": requirement.target_duration_ms,
            "intensity": requirement.intensity,
            "provider_id": SIMULATED_SOUND_EFFECT_PROVIDER_ID,
            "provider_version": SIMULATED_SOUND_EFFECT_PROVIDER_VERSION,
            "requirement_id": requirement.requirement_id,
            "sample_rate_hz": sample_rate_hz,
            "sample_width_bytes": sample_width_bytes,
            "schema_version": AUDIO_DESIGN_SCHEMA_VERSION,
        }
    )


def audio_design_plan_fingerprint(
    *,
    job_id: str,
    source_script_artifact_id: str,
    production_script_fingerprint: str,
    music_requirement: MusicRequirement | None,
    sound_effect_requirements: tuple[SoundEffectRequirement, ...],
    total_target_duration_ms: int,
) -> str:
    return canonical_sha256(
        {
            "job_id": job_id,
            "music_requirement": (
                music_requirement.model_dump(mode="json") if music_requirement is not None else None
            ),
            "production_script_fingerprint": production_script_fingerprint,
            "schema_version": AUDIO_DESIGN_SCHEMA_VERSION,
            "sound_effect_requirements": [
                item.model_dump(mode="json") for item in sound_effect_requirements
            ],
            "source_script_artifact_id": source_script_artifact_id,
            "total_target_duration_ms": total_target_duration_ms,
        }
    )


def verify_plan_fingerprint(plan: AudioDesignPlan) -> bool:
    return plan.plan_fingerprint == audio_design_plan_fingerprint(
        job_id=str(plan.job_id),
        source_script_artifact_id=str(plan.source_script_artifact_id),
        production_script_fingerprint=plan.production_script_fingerprint,
        music_requirement=plan.music_requirement,
        sound_effect_requirements=plan.sound_effect_requirements,
        total_target_duration_ms=plan.total_target_duration_ms,
    )
