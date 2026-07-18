"""Value objects for Orion AI domain."""

from dataclasses import dataclass
from enum import Enum, auto


class MediaFormat(Enum):
    MP4 = auto()
    MOV = auto()
    MKV = auto()
    AVI = auto()
    WEBM = auto()


class PlatformTarget(Enum):
    TIKTOK = "tiktok"
    YOUTUBE_SHORTS = "youtube_shorts"
    FACEBOOK_REELS = "facebook_reels"
    INSTAGRAM_REELS = "instagram_reels"


class Resolution:
    """Immutable video resolution."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height else 0.0

    def __repr__(self) -> str:
        return f"Resolution({self.width}x{self.height})"


@dataclass(frozen=True)
class FrameEmbedding:
    """Embedding vector for a video frame."""

    frame_index: int
    timestamp: float
    vector: list[float]
    model_id: str


@dataclass(frozen=True)
class ViralScore:
    """Score representing viral potential of a segment."""

    overall: float
    visual_impact: float
    audio_impact: float
    emotion: float
    context: float
    movement: float
    speech: float
    novelty: float


@dataclass(frozen=True)
class EmotionScore:
    """Emotion scoring for a temporal segment."""

    timestamp: float
    dominant: str
    scores: dict[str, float]
