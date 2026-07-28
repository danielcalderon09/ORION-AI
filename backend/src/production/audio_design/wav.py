"""Strict context-owned validation for canonical PCM WAV output."""

import io
import struct
import wave
from dataclasses import dataclass

from backend.src.production.audio_design.duration import duration_for_frame_count
from backend.src.production.audio_design.exceptions import AudioDesignWavError
from backend.src.production.audio_design.models import AudioPcmMetadata


@dataclass(frozen=True, slots=True)
class AudioWavExpectations:
    sample_rate_hz: int
    channel_count: int
    sample_width_bytes: int
    frame_count: int
    duration_ms: int


class AudioDesignWavValidator:
    def __init__(self, *, max_audio_bytes: int, max_peak_amplitude: int = 30_000) -> None:
        if not 44 <= max_audio_bytes <= 50_000_000:
            raise ValueError("maximum audio-design WAV size is outside safe limits")
        if not 1 <= max_peak_amplitude <= 32_767:
            raise ValueError("maximum audio-design peak amplitude is invalid")
        self._maximum = max_audio_bytes
        self._maximum_peak = max_peak_amplitude

    def validate(
        self,
        content: bytes,
        *,
        expected: AudioWavExpectations,
    ) -> AudioPcmMetadata:
        if not content or len(content) > self._maximum:
            raise AudioDesignWavError("audio-design WAV size is outside safe limits")
        expected_size = (
            44 + expected.frame_count * expected.channel_count * expected.sample_width_bytes
        )
        if (
            len(content) != expected_size
            or content[:4] != b"RIFF"
            or content[8:12] != b"WAVE"
            or content[12:16] != b"fmt "
            or content[36:40] != b"data"
            or int.from_bytes(content[4:8], "little") + 8 != len(content)
            or int.from_bytes(content[40:44], "little") != len(content) - 44
        ):
            raise AudioDesignWavError("audio-design WAV container is non-canonical")
        try:
            stream = io.BytesIO(content)
            with wave.open(stream, "rb") as reader:
                if reader.getcomptype() != "NONE":
                    raise AudioDesignWavError("audio-design WAV must use PCM")
                channels = reader.getnchannels()
                width = reader.getsampwidth()
                rate = reader.getframerate()
                frames = reader.getnframes()
                payload = reader.readframes(frames)
                if reader.readframes(1):
                    raise AudioDesignWavError("audio-design WAV has unexpected frames")
        except (EOFError, wave.Error) as exc:
            raise AudioDesignWavError("audio-design WAV is invalid") from exc
        if (
            channels != expected.channel_count
            or width != expected.sample_width_bytes
            or rate != expected.sample_rate_hz
            or frames != expected.frame_count
            or len(payload) != frames * channels * width
        ):
            raise AudioDesignWavError("audio-design WAV metadata differs")
        if rate != 24_000 or channels != 1 or width != 2:
            raise AudioDesignWavError("audio-design WAV must use the baseline format")
        peak = max(abs(value[0]) for value in struct.iter_unpack("<h", payload))
        if peak < 1:
            raise AudioDesignWavError("audio-design WAV must contain non-silent frames")
        if peak > self._maximum_peak:
            raise AudioDesignWavError("audio-design WAV exceeds the safe peak")
        duration_ms = duration_for_frame_count(frames, rate)
        if abs(duration_ms - expected.duration_ms) > 1:
            raise AudioDesignWavError("audio-design WAV duration differs")
        return AudioPcmMetadata(
            duration_ms=duration_ms,
            sample_rate_hz=24_000,
            channel_count=1,
            sample_width_bytes=2,
            frame_count=frames,
            peak_amplitude=peak,
        )
