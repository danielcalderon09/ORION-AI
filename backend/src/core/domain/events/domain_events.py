"""Domain events for event-driven communication."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class DomainEvent:
    """Base class for all domain events."""

    event_id: UUID
    project_id: UUID
    occurred_at: datetime
    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class VideoSubmittedEvent(DomainEvent):
    """Emitted when a new video is submitted for processing."""

    file_path: str
    project_name: str


@dataclass(frozen=True)
class PipelineStageCompletedEvent(DomainEvent):
    """Emitted when a pipeline stage finishes."""

    stage_name: str
    duration_seconds: float
    result_summary: dict[str, Any]


@dataclass(frozen=True)
class PipelineStageFailedEvent(DomainEvent):
    """Emitted when a pipeline stage fails."""

    stage_name: str
    error_message: str


@dataclass(frozen=True)
class ClipGeneratedEvent(DomainEvent):
    """Emitted when a clip is generated."""

    clip_id: UUID
    temporal_range: tuple[float, float]
    viral_score: float | None


@dataclass(frozen=True)
class ClipExportedEvent(DomainEvent):
    """Emitted when a clip is exported."""

    clip_id: UUID
    export_path: str
    format_settings: dict[str, Any]


@dataclass(frozen=True)
class QAValidationCompletedEvent(DomainEvent):
    """Emitted when QA finishes validating a clip."""

    clip_id: UUID
    passed: bool
    checks: list[dict[str, Any]]


@dataclass(frozen=True)
class UserFeedbackRecordedEvent(DomainEvent):
    """Emitted when user provides feedback on a clip."""

    clip_id: UUID
    feedback_type: str  # liked, disliked, adjusted, exported
    metadata: dict[str, Any]
