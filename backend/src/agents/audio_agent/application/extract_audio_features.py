"""Audio Agent - Extracts audio features and events."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.infrastructure.config.settings import settings
from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter


class IAudioFeatureProvider(Protocol):
    """Provider for audio feature extraction."""
    async def extract_features(self, audio_path: Path) -> dict: ...


@dataclass
class AudioAgentConfig:
    sample_rate: int = 16000
    extract_energy: bool = True
    detect_peaks: bool = True


class LibrosaAudioProvider:
    """Audio feature extraction using librosa."""

    async def extract_features(self, audio_path: Path) -> dict:
        import librosa

        y, sr = librosa.load(str(audio_path), sr=settings.DEFAULT_AUDIO_SAMPLE_RATE)

        # Energy (RMS)
        rms = librosa.feature.rms(y=y)[0]
        rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=512)

        # Onset strength
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        onset_times = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr, hop_length=512)

        # Detect peaks
        peaks = []
        threshold = settings.AUDIO_ENERGY_THRESHOLD * np.max(rms) if np.max(rms) > 0 else 0.1
        for i, val in enumerate(rms):
            if val > threshold:
                peaks.append({
                    "time": float(rms_times[i]),
                    "energy": float(val),
                })

        return {
            "rms_energy": {
                "times": rms_times.tolist(),
                "values": rms.tolist(),
            },
            "onset_strength": {
                "times": onset_times.tolist(),
                "values": onset_env.tolist(),
            },
            "peaks": peaks,
            "duration": float(len(y) / sr),
            "sample_rate": sr,
        }


class DummyAudioProvider:
    """Deterministic audio features for tests and offline contract checks."""

    async def extract_features(self, audio_path: Path) -> dict:
        return {
            "rms_energy": {"times": [0.0, 1.0, 2.0], "values": [0.2, 0.8, 0.4]},
            "onset_strength": {"times": [0.0, 1.0], "values": [0.1, 0.8]},
            "peaks": [{"time": 1.0, "energy": 0.8}],
            "duration": 3.0,
            "sample_rate": settings.DEFAULT_AUDIO_SAMPLE_RATE,
        }


class AudioAgent(IAgent):
    """Agent responsible for audio perception."""

    def __init__(
        self,
        media_adapter: FFmpegMediaAdapter,
        audio_provider: IAudioFeatureProvider | None = None,
        config: AudioAgentConfig | None = None,
    ):
        self.media_adapter = media_adapter
        self.audio_provider = audio_provider or LibrosaAudioProvider()
        self.config = config or AudioAgentConfig()

    @property
    def agent_id(self) -> str:
        return "audio_agent"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PERCEPTION

    def get_capabilities(self) -> list[str]:
        return ["audio_extraction", "energy_timeline", "peak_detection"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        video_path = Path(input_data.media_reference)

        # Extract audio
        from backend.src.infrastructure.config.settings import settings
        temp_audio = settings.TEMP_DIR / f"{video_path.stem}_audio.wav"
        audio_path = self.media_adapter.extract_audio(video_path, temp_audio, self.config.sample_rate)

        # Extract features
        features = await self.audio_provider.extract_features(audio_path)
        duration = features.get("duration", 0.0)

        temporal_range = input_data.temporal_range or (0.0, duration)

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.1.0",
            capability=self.capability,
            temporal_range=temporal_range,
            features=features,
        )
