"""Speech Agent - Transcribes audio to text using Faster Whisper."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from backend.src.agents.base.i_agent import AgentCapability, AgentInput, AgentResult, IAgent
from backend.src.infrastructure.config.settings import settings


class ISpeechRecognitionProvider(Protocol):
    """Provider for speech-to-text models."""
    async def transcribe(self, audio_path: Path, language: str | None = None) -> dict: ...


@dataclass
class SpeechAgentConfig:
    language: str | None = None  # auto-detect if None
    model_size: str = "base"
    device: str = "auto"
    compute_type: str = "int8"


class FasterWhisperProvider:
    """Faster Whisper implementation."""

    def __init__(self, model_size: str = "base", device: str = "auto", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=str(settings.MODELS_DIR / "whisper"),
            )
        return self._model

    async def transcribe(self, audio_path: Path, language: str | None = None) -> dict:
        model = self._load_model()
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            word_timestamps=True,
        )

        segments_list = []
        words_list = []
        for segment in segments:
            seg_dict = {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
                "confidence": segment.avg_logprob,
                "words": [],
            }
            if segment.words:
                for word in segment.words:
                    w = {
                        "word": word.word.strip(),
                        "start": word.start,
                        "end": word.end,
                        "confidence": word.probability,
                    }
                    seg_dict["words"].append(w)
                    words_list.append(w)
            segments_list.append(seg_dict)

        return {
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration,
            "segments": segments_list,
            "words": words_list,
            "transcript": " ".join(s["text"] for s in segments_list),
        }


class DummySpeechProvider:
    """Placeholder provider for testing."""

    async def transcribe(self, audio_path: Path, language: str | None = None) -> dict:
        return {
            "language": "en",
            "language_probability": 1.0,
            "duration": 0.0,
            "segments": [],
            "words": [],
            "transcript": "",
        }


class SpeechAgent(IAgent):
    """Agent responsible for speech-to-text."""

    def __init__(
        self,
        speech_provider: ISpeechRecognitionProvider | None = None,
        config: SpeechAgentConfig | None = None,
    ):
        self.speech_provider = speech_provider or FasterWhisperProvider(
            model_size=settings.WHISPER_MODEL_SIZE,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE_TYPE,
        )
        self.config = config or SpeechAgentConfig()

    @property
    def agent_id(self) -> str:
        return "speech_agent"

    @property
    def capability(self) -> AgentCapability:
        return AgentCapability.PERCEPTION

    def get_capabilities(self) -> list[str]:
        return ["speech_recognition", "transcription", "word_timestamps"]

    async def execute(self, input_data: AgentInput) -> AgentResult:
        video_path = Path(input_data.media_reference)

        # Extract audio first (re-use or extract)
        from backend.src.infrastructure.media.ffmpeg_adapter import FFmpegMediaAdapter
        media_adapter = FFmpegMediaAdapter()
        temp_audio = settings.TEMP_DIR / f"{video_path.stem}_speech.wav"
        audio_path = media_adapter.extract_audio(video_path, temp_audio)

        features = await self.speech_provider.transcribe(
            audio_path, language=self.config.language
        )
        duration = features.get("duration", 0.0)

        temporal_range = input_data.temporal_range or (0.0, duration)

        return AgentResult(
            agent_id=self.agent_id,
            agent_version="0.1.0",
            capability=self.capability,
            temporal_range=temporal_range,
            features=features,
        )
