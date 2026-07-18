"""Human Feedback domain model."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass
class StructuredFeedback:
    """User feedback on a produced clip."""
    feedback_id: str
    project_id: UUID
    clip_id: str
    overall_rating: int  # 1-5
    axis_ratings: dict[str, int]  # hook, pacing, subtitles, crop, audio, overall
    action_taken: str  # exported, discarded, re_edited, shared
    freeform_comment: str | None
    created_at: datetime
    platform: str


@dataclass
class FeedbackSummary:
    """Aggregated feedback statistics."""
    total_clips: int
    avg_overall_rating: float
    export_rate: float
    discard_rate: float
    top_issues: list[tuple[str, float]]  # (issue, frequency)
    improvement_trends: dict[str, float]  # axis -> rating trend


class IFeedbackCollector(Protocol):
    """Collects and stores human feedback."""
    def record_feedback(self, feedback: StructuredFeedback) -> None: ...
    def get_feedback_for_clip(self, clip_id: str) -> list[StructuredFeedback]: ...
    def get_project_summary(self, project_id: UUID) -> FeedbackSummary: ...
    def get_category_summary(self, category: str) -> FeedbackSummary: ...


class IFeedbackLearner(Protocol):
    """Learns from accumulated feedback to adjust creative weights."""
    async def train_on_feedback(self, feedback_batch: list[StructuredFeedback]) -> dict[str, Any]: ...
    async def suggest_weight_adjustments(self, category: str, platform: str) -> dict[str, float]: ...
    async def predict_user_preference(self, content_features: dict[str, Any]) -> dict[str, float]: ...
