"""Ports (interfaces) for Auto Reframe & Subject Tracking providers."""

from typing import Protocol
from backend.src.agents.dop_agent.domain.reframe import FaceBox, SubjectTrack, CropBox


class IFaceDetectionProvider(Protocol):
    """Provider for face detection in video frames."""

    async def detect_faces(self, frame) -> list[FaceBox]:
        """Detect faces in a single frame. Returns list of FaceBox."""
        ...


class ISubjectTrackingProvider(Protocol):
    """Provider for tracking subjects across video frames."""

    async def track_subjects(self, face_boxes_per_frame: list[list[FaceBox]]) -> list[SubjectTrack]:
        """Track subjects across frames using face detections. Returns list of SubjectTrack."""
        ...


class IAutoReframeProvider(Protocol):
    """Provider for intelligent auto reframe to vertical 9:16."""

    async def compute_reframe(
        self,
        video_path: str,
        temporal_range: tuple[float, float],
        target_width: int,
        target_height: int,
        face_boxes_per_frame: list[list[FaceBox]] | None = None,
        subject_tracks: list[SubjectTrack] | None = None,
        video_width: int | None = None,
        video_height: int | None = None,
    ) -> list[CropBox]:
        """Compute crop boxes for each frame or keyframes in the temporal range."""
        ...
