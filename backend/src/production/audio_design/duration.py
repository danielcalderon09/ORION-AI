"""Integer frame and cue-duration policies for offline audio design."""

from decimal import ROUND_HALF_UP, Decimal

from backend.src.production.audio_design.models import SoundEffectCueType

DEFAULT_SOUND_EFFECT_DURATION_MS: dict[SoundEffectCueType, int] = {
    SoundEffectCueType.TRANSITION: 500,
    SoundEffectCueType.IMPACT: 350,
    SoundEffectCueType.RISE: 1_000,
    SoundEffectCueType.ALERT: 400,
    SoundEffectCueType.AMBIENCE: 2_000,
    SoundEffectCueType.WHOOSH: 600,
    SoundEffectCueType.SOFT_CLICK: 120,
}


def seconds_to_milliseconds(value: float) -> int:
    return int((Decimal(str(value)) * 1_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def frame_count_for_duration(duration_ms: int, sample_rate_hz: int) -> int:
    if duration_ms <= 0:
        raise ValueError("audio duration must be positive")
    if sample_rate_hz <= 0:
        raise ValueError("sample rate must be positive")
    return max(1, (duration_ms * sample_rate_hz + 500) // 1_000)


def duration_for_frame_count(frame_count: int, sample_rate_hz: int) -> int:
    if frame_count <= 0 or sample_rate_hz <= 0:
        raise ValueError("frame count and sample rate must be positive")
    return (frame_count * 1_000 + sample_rate_hz // 2) // sample_rate_hz
