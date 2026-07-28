"""Strict standard-library PCM WAV inspection."""

import io
import wave
from dataclasses import dataclass

from backend.src.production.speech_generation.exceptions import (
    SpeechAudioIntegrityError,
)
from backend.src.production.speech_generation.models import (
    SpeechSegmentAudioMetadata,
)


@dataclass(frozen=True, slots=True)
class WavExpectations:
    sample_rate_hz: int
    channel_count: int
    sample_width_bytes: int
    frame_count: int | None = None
    duration_ms: int | None = None


class SpeechWavValidator:
    def __init__(self, *, max_audio_bytes: int) -> None:
        if not 44 <= max_audio_bytes <= 50_000_000:
            raise ValueError("maximum speech audio size is outside safe limits")
        self._maximum = max_audio_bytes

    def validate(
        self,
        content: bytes,
        *,
        expected: WavExpectations,
    ) -> SpeechSegmentAudioMetadata:
        if not content or len(content) > self._maximum:
            raise SpeechAudioIntegrityError("speech WAV size is outside safe limits")
        if (
            len(content) < 44
            or content[:4] != b"RIFF"
            or content[8:12] != b"WAVE"
            or int.from_bytes(content[4:8], "little") + 8 != len(content)
        ):
            raise SpeechAudioIntegrityError("speech WAV container boundaries are invalid")
        try:
            stream = io.BytesIO(content)
            with wave.open(stream, "rb") as reader:
                if reader.getcomptype() != "NONE":
                    raise SpeechAudioIntegrityError("speech WAV must use PCM")
                channels = reader.getnchannels()
                width = reader.getsampwidth()
                rate = reader.getframerate()
                frames = reader.getnframes()
                payload = reader.readframes(frames)
                if reader.readframes(1):
                    raise SpeechAudioIntegrityError("speech WAV has unexpected frames")
        except (EOFError, wave.Error) as exc:
            raise SpeechAudioIntegrityError("speech WAV is invalid") from exc
        expected_payload = frames * channels * width
        if len(payload) != expected_payload or frames < 1:
            raise SpeechAudioIntegrityError("speech WAV frame data is incomplete")
        if (
            rate != expected.sample_rate_hz
            or channels != expected.channel_count
            or width != expected.sample_width_bytes
        ):
            raise SpeechAudioIntegrityError("speech WAV format differs from configuration")
        if expected.frame_count is not None and frames != expected.frame_count:
            raise SpeechAudioIntegrityError("speech WAV frame count differs from contract")
        duration_ms = round(frames * 1_000 / rate)
        if expected.duration_ms is not None and abs(duration_ms - expected.duration_ms) > 1:
            raise SpeechAudioIntegrityError("speech WAV duration differs from contract")
        return SpeechSegmentAudioMetadata(
            duration_ms=duration_ms,
            sample_rate_hz=rate,
            channel_count=1,
            sample_width_bytes=2,
            frame_count=frames,
        )
