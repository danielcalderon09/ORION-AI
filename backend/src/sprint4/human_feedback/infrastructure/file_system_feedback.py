"""Human Feedback implementation."""

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from backend.src.sprint4.human_feedback.domain.structured_feedback import (
    FeedbackSummary, IFeedbackCollector, IFeedbackLearner, StructuredFeedback,
)
from backend.src.infrastructure.config.settings import settings


class FileSystemFeedbackCollector(IFeedbackCollector):
    """Collects and stores human feedback on filesystem."""

    def __init__(self):
        self.feedback_dir = settings.ORION_HOME / "human_feedback"
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self._feedback: list[StructuredFeedback] = []
        self._load_all()

    def record_feedback(self, feedback: StructuredFeedback) -> None:
        self._feedback.append(feedback)
        self._save_feedback(feedback)

    def get_feedback_for_clip(self, clip_id: str) -> list[StructuredFeedback]:
        return [f for f in self._feedback if f.clip_id == clip_id]

    def get_project_summary(self, project_id: UUID) -> FeedbackSummary:
        project_feedback = [f for f in self._feedback if f.project_id == project_id]
        return self._compute_summary(project_feedback)

    def get_category_summary(self, category: str) -> FeedbackSummary:
        # Category not stored directly in feedback; would need project lookup
        # For now, return global summary
        return self._compute_summary(self._feedback)

    def _compute_summary(self, feedback_list: list[StructuredFeedback]) -> FeedbackSummary:
        if not feedback_list:
            return FeedbackSummary(total_clips=0, avg_overall_rating=0, export_rate=0, discard_rate=0, top_issues=[], improvement_trends={})

        total = len(feedback_list)
        ratings = [f.overall_rating for f in feedback_list]
        exported = sum(1 for f in feedback_list if f.action_taken == "exported")
        discarded = sum(1 for f in feedback_list if f.action_taken == "discarded")

        # Top issues from axis ratings below 3
        issue_counts: dict[str, int] = {}
        for f in feedback_list:
            for axis, rating in f.axis_ratings.items():
                if rating < 3:
                    issue_counts[axis] = issue_counts.get(axis, 0) + 1
        top_issues = sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        issue_freq = [(axis, count / total) for axis, count in top_issues]

        return FeedbackSummary(
            total_clips=total,
            avg_overall_rating=sum(ratings) / total,
            export_rate=exported / total,
            discard_rate=discarded / total,
            top_issues=issue_freq,
            improvement_trends={},  # Would need time-series analysis
        )

    def _save_feedback(self, feedback: StructuredFeedback) -> None:
        path = self.feedback_dir / f"{feedback.feedback_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "feedback_id": feedback.feedback_id,
                "project_id": str(feedback.project_id),
                "clip_id": feedback.clip_id,
                "overall_rating": feedback.overall_rating,
                "axis_ratings": feedback.axis_ratings,
                "action_taken": feedback.action_taken,
                "freeform_comment": feedback.freeform_comment,
                "created_at": feedback.created_at.isoformat(),
                "platform": feedback.platform,
            }, f, indent=2, default=str)

    def _load_all(self) -> None:
        for path in self.feedback_dir.glob("*.json"):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._feedback.append(StructuredFeedback(
                feedback_id=data["feedback_id"],
                project_id=UUID(data["project_id"]),
                clip_id=data["clip_id"],
                overall_rating=data["overall_rating"],
                axis_ratings=data.get("axis_ratings", {}),
                action_taken=data["action_taken"],
                freeform_comment=data.get("freeform_comment"),
                created_at=datetime.fromisoformat(data["created_at"]),
                platform=data.get("platform", "unknown"),
            ))


class SimpleFeedbackLearner(IFeedbackLearner):
    """Learns from feedback to suggest weight adjustments."""

    def __init__(self, collector: FileSystemFeedbackCollector | None = None):
        self.collector = collector or FileSystemFeedbackCollector()

    async def train_on_feedback(self, feedback_batch: list[StructuredFeedback]) -> dict:
        """Simple heuristic training: find axes that correlate with low ratings."""
        if not feedback_batch:
            return {"status": "no_data"}

        # Compute average rating per axis
        axis_sums: dict[str, float] = {}
        axis_counts: dict[str, int] = {}
        for fb in feedback_batch:
            for axis, rating in fb.axis_ratings.items():
                axis_sums[axis] = axis_sums.get(axis, 0) + rating
                axis_counts[axis] = axis_counts.get(axis, 0) + 1

        axis_avgs = {axis: axis_sums[axis] / axis_counts[axis] for axis in axis_sums}

        # Suggest increasing weights for axes with low averages (need more attention)
        adjustments = {}
        for axis, avg in axis_avgs.items():
            if avg < 2.5:
                adjustments[axis] = 1.2  # increase weight by 20%
            elif avg > 4.0:
                adjustments[axis] = 0.9  # decrease slightly (already good)
            else:
                adjustments[axis] = 1.0

        return {
            "status": "trained",
            "axis_avgs": axis_avgs,
            "adjustments": adjustments,
            "sample_size": len(feedback_batch),
        }

    async def suggest_weight_adjustments(self, category: str, platform: str) -> dict[str, float]:
        """Suggest creative weight adjustments based on historical feedback."""
        # Filter feedback for category/platform (simplified: use all for now)
        summary = self.collector.get_category_summary(category)
        adjustments = {}

        # If discard rate is high, increase hook and pacing weights
        if summary.discard_rate > 0.3:
            adjustments["hook_weight"] = 1.3
            adjustments["pacing_weight"] = 1.2

        # If top issue is subtitles, increase subtitle emphasis
        if summary.top_issues and summary.top_issues[0][0] == "subtitles":
            adjustments["subtitle_weight"] = 1.3

        return adjustments

    async def predict_user_preference(self, content_features: dict) -> dict[str, float]:
        """Predict what the user will likely prefer based on past feedback."""
        # Simplified: return default preferences
        return {
            "prefers_fast_pacing": 0.7,
            "prefers_animated_subs": 0.8,
            "prefers_strong_hooks": 0.9,
        }
