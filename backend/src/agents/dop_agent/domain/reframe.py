"""Domain models for Auto Reframe & Subject Tracking."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class FaceBox:
    """Detected face bounding box and landmarks."""
    x: int
    y: int
    width: int
    height: int
    confidence: float
    face_id: Optional[int] = None


@dataclass
class SubjectTrack:
    """Tracked subject across frames."""
    track_id: int
    face_boxes: list[FaceBox]
    start_frame: int
    end_frame: int
    center_x: float  # Average center X across frames
    center_y: float  # Average center Y across frames


@dataclass
class CropBox:
    """Crop parameters for a single frame or clip."""
    x: int
    y: int
    width: int
    height: int
    confidence: float = 1.0


@dataclass
class ReframeDecision:
    """Final reframe decision for a clip."""
    clip_id: str
    temporal_range: tuple[float, float]
    crop_boxes: list[CropBox]  # Per-frame or keyframe crops
    target_width: int
    target_height: int
    tracking_enabled: bool
    primary_subject: Optional[int] = None
    fallback_reason: Optional[str] = None
