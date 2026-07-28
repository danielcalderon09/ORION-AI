"""Deterministic offline placeholder music provider."""

import asyncio
import hashlib

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
)
from backend.src.production.audio_design.fingerprints import (
    SIMULATED_MUSIC_PROVIDER_ID,
)
from backend.src.production.audio_design.models import (
    AudioPcmMetadata,
    GeneratedAudioResult,
    MusicGenerationRequest,
)
from backend.src.production.audio_design.waveform import render_music_wav


class SimulatedMusicGenerationProvider:
    """Generate an abstract harmonic placeholder, never an imitation."""

    provider_id = SIMULATED_MUSIC_PROVIDER_ID

    def __init__(self, configuration: AudioDesignConfiguration) -> None:
        self._configuration = configuration
        self._closed = False

    async def generate(self, request: MusicGenerationRequest) -> GeneratedAudioResult:
        if self._closed:
            raise AudioDesignProviderClosedError("music provider is closed")
        if not (
            self._configuration.min_music_duration_ms
            <= request.duration_ms
            <= self._configuration.max_music_duration_ms
        ):
            raise AudioDesignProviderResponseError("music duration is outside safe limits")
        await asyncio.sleep(0)
        frame_count = frame_count_for_duration(
            request.duration_ms,
            request.sample_rate_hz,
        )
        size = 44 + frame_count * request.channel_count * request.sample_width_bytes
        if size > self._configuration.max_audio_bytes:
            raise AudioDesignProviderResponseError("music output exceeds safe size")
        content = await asyncio.to_thread(
            render_music_wav,
            fingerprint=request.request_fingerprint,
            intensity=request.intensity,
            loopable=request.loopable,
            sample_rate_hz=request.sample_rate_hz,
            frame_count=frame_count,
        )
        peak = max(
            abs(int.from_bytes(content[index : index + 2], "little", signed=True))
            for index in range(44, len(content), 2)
        )
        return GeneratedAudioResult(
            provider_id=self.provider_id,
            provider_asset_id=f"sim-music-{request.request_fingerprint[:24]}",
            content=content,
            audio=AudioPcmMetadata(
                duration_ms=duration_for_frame_count(
                    frame_count,
                    request.sample_rate_hz,
                ),
                frame_count=frame_count,
                peak_amplitude=peak,
            ),
            sha256=hashlib.sha256(content).hexdigest(),
            metadata={
                "copyrighted_sample": False,
                "deterministic": True,
                "network": False,
                "waveform": "integer_harmonic_pattern",
            },
        )

    async def close(self) -> None:
        self._closed = True
