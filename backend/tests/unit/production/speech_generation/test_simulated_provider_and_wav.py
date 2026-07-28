import asyncio

import pytest
from pydantic import ValidationError

from backend.src.production.speech_generation.duration import simulated_duration_ms
from backend.src.production.speech_generation.exceptions import (
    SpeechAudioIntegrityError,
    SpeechProviderClosedError,
)
from backend.src.production.speech_generation.ports import SpeechProviderRequest
from backend.src.production.speech_generation.providers import (
    SimulatedSpeechGenerationProvider,
)
from backend.src.production.speech_generation.segment_builder import (
    build_speech_segments,
)
from backend.src.production.speech_generation.wav import (
    SpeechWavValidator,
    WavExpectations,
)
from backend.tests.unit.production.speech_generation.conftest import (
    COMMAND_ID,
    JOB_ID,
    source_script,
    speech_configuration,
)


def _request(*, text: str = "Hola, mundo.") -> SpeechProviderRequest:
    configuration = speech_configuration()
    source = source_script(first_narration=text)
    segment = build_speech_segments(source, configuration)[0]
    return SpeechProviderRequest(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        correlation_id=JOB_ID,
        attempt_number=1,
        segment=segment,
        configuration=configuration,
        fingerprint=configuration.fingerprint(),
    )


async def test_simulated_provider_returns_valid_deterministic_pcm_wav() -> None:
    provider = SimulatedSpeechGenerationProvider()
    request = _request()
    first = await provider.generate(request)
    second = await provider.generate(request)

    assert first.content == second.content
    assert first.content.startswith(b"RIFF")
    assert first.content[8:12] == b"WAVE"
    assert first.audio.sample_rate_hz == 24_000
    assert first.audio.channel_count == 1
    assert first.audio.sample_width_bytes == 2
    inspected = SpeechWavValidator(max_audio_bytes=request.configuration.max_audio_bytes).validate(
        first.content,
        expected=WavExpectations(
            sample_rate_hz=24_000,
            channel_count=1,
            sample_width_bytes=2,
            frame_count=first.audio.frame_count,
            duration_ms=first.audio.duration_ms,
        ),
    )
    assert inspected == first.audio


async def test_changed_text_changes_deterministic_audio() -> None:
    provider = SimulatedSpeechGenerationProvider()
    first = await provider.generate(_request(text="Hola, mundo."))
    second = await provider.generate(_request(text="Hola, universo."))
    assert first.content != second.content


def test_explicit_script_duration_is_clamped() -> None:
    request = _request()
    assert simulated_duration_ms(request) == 500
    shorter = request.model_copy(
        update={"segment": request.segment.model_copy(update={"target_duration_ms": 1})}
    )
    assert simulated_duration_ms(shorter) == request.configuration.min_duration_ms


async def test_close_is_idempotent_and_generation_after_close_fails() -> None:
    provider = SimulatedSpeechGenerationProvider()
    await provider.close()
    await provider.close()
    with pytest.raises(SpeechProviderClosedError):
        await provider.generate(_request())


async def test_cancellation_is_not_swallowed() -> None:
    provider = SimulatedSpeechGenerationProvider()
    task = asyncio.create_task(provider.generate(_request()))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_wav_validator_rejects_corruption_and_trailing_content() -> None:
    result = await SimulatedSpeechGenerationProvider().generate(_request())
    validator = SpeechWavValidator(max_audio_bytes=200_000)
    expected = WavExpectations(
        sample_rate_hz=24_000,
        channel_count=1,
        sample_width_bytes=2,
    )
    for content in (result.content[:-2], result.content + b"x", b"not-a-wave"):
        with pytest.raises(SpeechAudioIntegrityError):
            validator.validate(content, expected=expected)


def test_contract_raw_audio_is_excluded_from_repr_and_serialization() -> None:
    from backend.src.production.speech_generation.models import (
        SpeechSegmentAudioMetadata,
    )
    from backend.src.production.speech_generation.ports import SpeechProviderResult

    result = SpeechProviderResult(
        content=b"secret-audio",
        provider="orion-simulated-speech",
        audio=SpeechSegmentAudioMetadata(
            duration_ms=250,
            sample_rate_hz=24_000,
            frame_count=6_000,
        ),
        deterministic=True,
    )
    assert "secret-audio" not in repr(result)
    assert "content" not in result.model_dump()
    with pytest.raises(ValidationError):
        SpeechProviderResult(
            content=b"safe",
            provider="orion-simulated-speech",
            audio=result.audio,
            deterministic=True,
            metadata={"api_key": "obviously-fake"},
        )
