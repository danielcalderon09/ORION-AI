"""Deterministic integer PCM synthesis without external samples or libraries."""

from __future__ import annotations

import io
import struct
import wave

from backend.src.production.audio_design.models import SoundEffectCueType


def render_music_wav(
    *,
    fingerprint: str,
    intensity: int,
    loopable: bool,
    sample_rate_hz: int,
    frame_count: int,
) -> bytes:
    seed = bytes.fromhex(fingerprint)
    base_frequency = 110 + seed[0] % 60
    chord_offsets = (0, 4 + seed[1] % 4, 7 + seed[2] % 5)
    amplitude = 1_800 + intensity * 38
    beat_frames = max(1, sample_rate_hz // 4)
    attack_release_frames = min(frame_count // 4, sample_rate_hz // 10)
    frames = bytearray(frame_count * 2)
    for index in range(frame_count):
        chord_index = (index // (beat_frames * 2)) % 4
        modulation = 75 if (index // beat_frames) % 2 == 0 else 55
        value = 0
        for harmonic_index, offset in enumerate(chord_offsets):
            frequency = base_frequency + offset * 7 + chord_index * 3
            period = max(2, sample_rate_hz // frequency)
            component = _triangle(index, period, amplitude // (harmonic_index + 2))
            value += component
        value = value * modulation // 100
        if not loopable and attack_release_frames:
            envelope = min(
                1_000,
                index * 1_000 // attack_release_frames,
                (frame_count - index - 1) * 1_000 // attack_release_frames,
            )
            value = value * max(0, envelope) // 1_000
        struct.pack_into("<h", frames, index * 2, _clamp(value))
    return _wav(bytes(frames), sample_rate_hz=sample_rate_hz, frame_count=frame_count)


def render_sound_effect_wav(
    *,
    cue_type: SoundEffectCueType,
    fingerprint: str,
    intensity: int,
    sample_rate_hz: int,
    frame_count: int,
) -> bytes:
    seed = int(fingerprint[:8], 16) or 1
    amplitude = 2_000 + intensity * 65
    frames = bytearray(frame_count * 2)
    state = seed
    for index in range(frame_count):
        progress = index * 1_000 // max(1, frame_count - 1)
        if cue_type is SoundEffectCueType.IMPACT:
            envelope = (1_000 - progress) ** 2 // 1_000
            value = _triangle(index, max(2, sample_rate_hz // 95), amplitude)
            value = value * envelope // 1_000
        elif cue_type is SoundEffectCueType.RISE:
            frequency = 180 + progress * 520 // 1_000
            value = _triangle(index, max(2, sample_rate_hz // frequency), amplitude)
            value = value * progress // 1_000
        elif cue_type is SoundEffectCueType.TRANSITION:
            frequency = 650 - progress * 430 // 1_000
            envelope = 1_000 - abs(500 - progress) * 2
            value = _triangle(index, max(2, sample_rate_hz // frequency), amplitude)
            value = value * max(0, envelope) // 1_000
        elif cue_type is SoundEffectCueType.WHOOSH:
            frequency = 320 + progress * 580 // 1_000
            envelope = 1_000 - abs(500 - progress) * 2
            value = _triangle(index, max(2, sample_rate_hz // frequency), amplitude)
            value = value * max(0, envelope) // 1_000
        elif cue_type is SoundEffectCueType.ALERT:
            pulse = (index // max(1, sample_rate_hz // 8)) % 2
            value = _triangle(index, max(2, sample_rate_hz // 620), amplitude) if pulse == 0 else 0
        elif cue_type is SoundEffectCueType.AMBIENCE:
            state = (1_664_525 * state + 1_013_904_223) & 0xFFFFFFFF
            value = ((state >> 16) - 32_768) * amplitude // 65_536
            value += _triangle(index, max(2, sample_rate_hz // 90), amplitude // 4)
        elif cue_type is SoundEffectCueType.SOFT_CLICK:
            click_frames = max(1, sample_rate_hz // 100)
            value = (
                amplitude * (click_frames - index) // click_frames if index < click_frames else 0
            )
            if index % 2:
                value = -value
        else:  # pragma: no cover - enum exhaustiveness guard
            raise ValueError("unsupported sound-effect cue")
        struct.pack_into("<h", frames, index * 2, _clamp(value))
    return _wav(bytes(frames), sample_rate_hz=sample_rate_hz, frame_count=frame_count)


def _triangle(index: int, period: int, amplitude: int) -> int:
    phase = index % period
    half = max(1, period // 2)
    if phase < half:
        return -amplitude + (2 * amplitude * phase // half)
    remaining = max(1, period - half)
    return amplitude - (2 * amplitude * (phase - half) // remaining)


def _clamp(value: int) -> int:
    return max(-24_000, min(24_000, value))


def _wav(frames: bytes, *, sample_rate_hz: int, frame_count: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate_hz)
        writer.setnframes(frame_count)
        writer.writeframes(frames)
    return output.getvalue()
