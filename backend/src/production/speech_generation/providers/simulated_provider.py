"""Deterministic offline standard-library PCM WAV provider."""

import asyncio
import hashlib
import io
import struct
import wave

from backend.src.production.speech_generation.duration import simulated_duration_ms
from backend.src.production.speech_generation.exceptions import (
    SpeechProviderClosedError,
    SpeechProviderResponseError,
)
from backend.src.production.speech_generation.models import (
    SpeechSegmentAudioMetadata,
)
from backend.src.production.speech_generation.ports import (
    SpeechProviderRequest,
    SpeechProviderResult,
)


class SimulatedSpeechGenerationProvider:
    """Render a deterministic tone pattern; it is not human-quality speech."""

    name = "orion-simulated-speech"

    def __init__(self) -> None:
        self._closed = False

    async def generate(self, request: SpeechProviderRequest) -> SpeechProviderResult:
        if self._closed:
            raise SpeechProviderClosedError("speech provider is closed")
        await asyncio.sleep(0)
        duration_ms = simulated_duration_ms(request)
        configuration = request.configuration
        frame_count = max(
            1,
            round(duration_ms * configuration.sample_rate_hz / 1_000),
        )
        size = 44 + frame_count * configuration.channel_count * configuration.sample_width_bytes
        if size > configuration.max_audio_bytes:
            raise SpeechProviderResponseError(
                "simulated speech exceeds the configured output limit"
            )
        content = await asyncio.to_thread(
            _render_wav,
            request.segment.normalized_text_hash,
            sample_rate_hz=configuration.sample_rate_hz,
            frame_count=frame_count,
        )
        return SpeechProviderResult(
            content=content,
            provider=self.name,
            audio=SpeechSegmentAudioMetadata(
                duration_ms=round(frame_count * 1_000 / configuration.sample_rate_hz),
                sample_rate_hz=configuration.sample_rate_hz,
                channel_count=configuration.channel_count,
                sample_width_bytes=configuration.sample_width_bytes,
                frame_count=frame_count,
            ),
            deterministic=True,
            metadata={
                "simulated": True,
                "network": False,
                "waveform": "deterministic_tone_pattern",
            },
        )

    async def close(self) -> None:
        self._closed = True


def _render_wav(
    text_hash: str,
    *,
    sample_rate_hz: int,
    frame_count: int,
) -> bytes:
    seed = bytes.fromhex(text_hash)
    base_frequency = 180 + seed[0] * 2
    alternate_frequency = 260 + seed[1] * 3
    amplitude = 4_000 + seed[2] * 20
    block_frames = max(1, sample_rate_hz // 2)
    first = _tone_block(
        frequency=base_frequency,
        amplitude=amplitude,
        sample_rate_hz=sample_rate_hz,
        frame_count=block_frames,
    )
    second = _tone_block(
        frequency=alternate_frequency,
        amplitude=amplitude,
        sample_rate_hz=sample_rate_hz,
        frame_count=block_frames,
    )
    cycle = first + second
    cycle_frames = block_frames * 2
    repetitions, remainder = divmod(frame_count, cycle_frames)
    frames = cycle * repetitions + cycle[: remainder * 2]
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate_hz)
        writer.setnframes(frame_count)
        writer.writeframes(frames)
    result = output.getvalue()
    # The digest call deliberately ensures no use of Python's unstable hash().
    hashlib.sha256(result).digest()
    return result


def _tone_block(
    *,
    frequency: int,
    amplitude: int,
    sample_rate_hz: int,
    frame_count: int,
) -> bytes:
    frames = bytearray(frame_count * 2)
    silence_frames = min(max(1, sample_rate_hz // 20), frame_count)
    tone_frames = frame_count - silence_frames
    period_frames = max(2, round(sample_rate_hz / frequency))
    positive_frames = max(1, period_frames // 2)
    negative_frames = period_frames - positive_frames
    period = (
        struct.pack("<h", amplitude) * positive_frames
        + struct.pack("<h", -amplitude) * negative_frames
    )
    repetitions, remainder = divmod(tone_frames, period_frames)
    tone = period * repetitions + period[: remainder * 2]
    frames[: silence_frames * 2] = b"\x00\x00" * silence_frames
    frames[silence_frames * 2 :] = tone
    return bytes(frames)
