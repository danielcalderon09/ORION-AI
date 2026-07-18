"""Domain entities for Orion AI projects."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


class ProjectStatus(Enum):
    CREATED = auto()
    INDEXING = auto()
    PERCEIVING = auto()
    UNDERSTANDING = auto()
    DIRECTING = auto()
    EXPORTING = auto()
    COMPLETED = auto()
    FAILED = auto()


@dataclass
class VideoAsset:
    """Represents the source video file."""

    file_path: Path
    duration_seconds: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None
    file_hash: str
    format_name: str


@dataclass
class TemporalRange:
    """A time range within a video."""

    start_seconds: float
    end_seconds: float

    @property
    def duration(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass
class VideoClip:
    """A generated clip ready for export."""

    clip_id: UUID = field(default_factory=uuid4)
    temporal_range: TemporalRange | None = None
    export_path: Path | None = None
    status: str = "pending"  # pending, validated, exported, failed
    qa_result: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NarrativeMemory:
    """Decisions and outputs from Narrative Intelligence Agent."""

    detected_beats: list[dict[str, Any]] = field(default_factory=list)
    arcs: list[dict[str, Any]] = field(default_factory=list)
    climax_moments: list[TemporalRange] = field(default_factory=list)


@dataclass
class DirectorMemory:
    """Decisions and outputs from Director AI."""

    creative_brief: dict[str, Any] = field(default_factory=dict)
    edit_decisions: list[dict[str, Any]] = field(default_factory=list)
    selected_clips: list[UUID] = field(default_factory=list)
    style_profile: str = "default"


@dataclass
class ExportRecord:
    """Record of an export operation."""

    export_id: UUID = field(default_factory=uuid4)
    clip_id: UUID | None = None
    output_path: Path | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    format_settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class UserPreferenceSnapshot:
    """User preferences at a point in time."""

    preferred_style: str = "auto"
    target_platform: str = "tiktok"
    target_duration: float | None = None
    custom_weights: dict[str, float] = field(default_factory=dict)


@dataclass
class QAValidation:
    """Result of QA validation for a clip or project."""

    validation_id: UUID = field(default_factory=uuid4)
    clip_id: UUID | None = None
    passed: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)
    validated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ProjectBrain:
    """Central memory of a project."""

    project_id: UUID
    project_name: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    asset: VideoAsset | None = None
    features_index: dict[str, Any] = field(default_factory=dict)
    narrative_memory: NarrativeMemory = field(default_factory=NarrativeMemory)
    director_memory: DirectorMemory = field(default_factory=DirectorMemory)
    export_history: list[ExportRecord] = field(default_factory=list)
    user_preferences: UserPreferenceSnapshot = field(default_factory=UserPreferenceSnapshot)
    quality_validations: list[QAValidation] = field(default_factory=list)
    status: ProjectStatus = ProjectStatus.CREATED
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class VideoProject:
    """Top-level project aggregate root."""

    project_id: UUID = field(default_factory=uuid4)
    name: str = "Untitled"
    source_path: Path | None = None
    workspace_path: Path | None = None
    brain: ProjectBrain | None = None
    clips: list[VideoClip] = field(default_factory=list)
    status: ProjectStatus = ProjectStatus.CREATED
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def initialize_brain(self) -> None:
        if self.brain is None:
            self.brain = ProjectBrain(
                project_id=self.project_id,
                project_name=self.name,
            )
