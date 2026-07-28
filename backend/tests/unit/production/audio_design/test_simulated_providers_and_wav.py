from __future__ import annotations

import asyncio
import hashlib
import struct

import pytest

from backend.src.production.audio_design.configuration import (
    AudioDesignConfiguration,
)
from backend.src.production.audio_design.duration import (
    duration_for_frame_count,
    frame_count_for_duration,
)
from backend.src.production.audio_design.exceptions import (
    AudioDesignProviderClosedError,
    AudioDesignProviderResponseError,
    AudioDesignWavError,
)
from backend.src.production.audio_design.models import (
    MusicGenerationRequest,
    MusicMood,
    SoundEffectCueType,
    SoundEffectGenerationRequest,
)
from backend.src.production.audio_design.providers import (
    SimulatedMusicGenerationProvider,
    SimulatedSoundEffectGenerationProvider,
)
from backend.src.production.audio_design.wav import (
    AudioDesignWavValidator,
    AudioWavExpectations,
)


def _configuration() -> AudioDesignConfiguration:
    return AudioDesignConfiguration(
        max_music_duration_ms=5_000,
        max_audio_bytes=300_000,
    )


def _music_request(
    *,
    fingerprint: str = "1" * 64,
    duration_ms: int = 1_000,
    intensity: int = 40,
) -> MusicGenerationRequest:
    return MusicGenerationRequest(
        request_id=f"music-request-{fingerprint[:24]}",
        requirement_id="music-" + "a" * 24,
        mood=MusicMood.CALM,
        intensity=intensity,
        duration_ms=duration_ms,
        loopable=True,
        request_fingerprint=fingerprint,
    )


def _sfx_request(
    cue: SoundEffectCueType,
    *,
    fingerprint: str | None = None,
    duration_ms: int = 300,
) -> SoundEffectGenerationRequest:
    digest = fingerprint or hashlib.sha256(cue.value.encode()).hexdigest()
    return SoundEffectGenerationRequest(
        request_id=f"sfx-request-{digest[:24]}",
        requirement_id="sfx-" + hashlib.sha256(cue.value.encode()).hexdigest()[:24],
        cue_type=cue,
        intensity=50,
        duration_ms=duration_ms,
        request_fingerprint=digest,
    )


def _expectations(duration_ms: int) -> AudioWavExpectations:
    frames = frame_count_for_duration(duration_ms, 24_000)
    return AudioWavExpectations(
        sample_rate_hz=24_000,
        channel_count=1,
        sample_width_bytes=2,
        frame_count=frames,
        duration_ms=duration_for_frame_count(frames, 24_000),
    )


@pytest.mark.asyncio
async def test_music_is_valid_deterministic_non_silent_bounded_pcm() -> None:
    provider = SimulatedMusicGenerationProvider(_configuration())
    request = _music_request()

    first = await provider.generate(request)
    second = await provider.generate(request)
    inspected = AudioDesignWavValidator(max_audio_bytes=300_000).validate(
        first.content,
        expected=_expectations(request.duration_ms),
    )
    samples = tuple(value[0] for value in struct.iter_unpack("<h", first.content[44:]))

    assert first.content == second.content
    assert first.sha256 == hashlib.sha256(first.content).hexdigest()
    assert inspected == first.audio
    assert inspected.frame_count == 24_000
    assert 0 < inspected.peak_amplitude <= 24_000
    assert min(samples) < 0 < max(samples)
    assert first.metadata["copyrighted_sample"] is False


@pytest.mark.asyncio
async def test_relevant_music_request_changes_alter_output() -> None:
    provider = SimulatedMusicGenerationProvider(_configuration())

    baseline = await provider.generate(_music_request())
    fingerprint_changed = await provider.generate(_music_request(fingerprint="2" * 64))
    intensity_changed = await provider.generate(_music_request(intensity=41))

    assert baseline.content != fingerprint_changed.content
    assert baseline.content != intensity_changed.content


@pytest.mark.asyncio
async def test_each_supported_sfx_is_valid_distinct_and_deterministic() -> None:
    provider = SimulatedSoundEffectGenerationProvider(_configuration())
    validator = AudioDesignWavValidator(max_audio_bytes=300_000)
    hashes: set[str] = set()

    for cue in SoundEffectCueType:
        request = _sfx_request(cue)
        first = await provider.generate(request)
        second = await provider.generate(request)
        inspected = validator.validate(
            first.content,
            expected=_expectations(request.duration_ms),
        )
        assert first.content == second.content
        assert inspected.frame_count == 7_200
        assert 0 < inspected.peak_amplitude <= 24_000
        assert first.metadata["realistic_recording"] is False
        hashes.add(first.sha256)

    assert len(hashes) == len(SoundEffectCueType)


def test_unknown_sfx_cue_is_rejected_by_strict_contract() -> None:
    with pytest.raises(ValueError):
        SoundEffectGenerationRequest(
            request_id="sfx-request-" + "a" * 24,
            requirement_id="sfx-" + "b" * 24,
            cue_type="explosion",  # type: ignore[arg-type]
            intensity=50,
            duration_ms=300,
            request_fingerprint="c" * 64,
        )


@pytest.mark.asyncio
async def test_provider_enforces_maximum_duration_and_size() -> None:
    provider = SimulatedMusicGenerationProvider(_configuration())

    with pytest.raises(AudioDesignProviderResponseError, match="duration"):
        await provider.generate(_music_request(duration_ms=5_001))


@pytest.mark.asyncio
async def test_provider_close_is_idempotent_and_fail_closed() -> None:
    music = SimulatedMusicGenerationProvider(_configuration())
    sound = SimulatedSoundEffectGenerationProvider(_configuration())

    await music.close()
    await music.close()
    await sound.close()
    await sound.close()

    with pytest.raises(AudioDesignProviderClosedError):
        await music.generate(_music_request())
    with pytest.raises(AudioDesignProviderClosedError):
        await sound.generate(_sfx_request(SoundEffectCueType.ALERT))


@pytest.mark.asyncio
async def test_generation_propagates_cancellation() -> None:
    provider = SimulatedMusicGenerationProvider(_configuration())
    task = asyncio.create_task(provider.generate(_music_request(duration_ms=5_000)))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_wav_validator_rejects_truncation_trailing_data_and_silence() -> None:
    result = await SimulatedMusicGenerationProvider(_configuration()).generate(_music_request())
    validator = AudioDesignWavValidator(max_audio_bytes=300_000)
    expected = _expectations(1_000)

    for content in (
        result.content[:-2],
        result.content + b"x",
        result.content[:44] + b"\x00" * (len(result.content) - 44),
    ):
        with pytest.raises(AudioDesignWavError):
            validator.validate(content, expected=expected)
