"""SQLAlchemy record for one result per stage command."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.production.infrastructure.persistence.models.base import ProductionBase
from backend.src.production.infrastructure.persistence.models.types import UTCDateTime


class StageResultRecord(ProductionBase):
    """Use command_id as the stable persistent identity of a StageResult."""

    __tablename__ = "stage_results"
    __table_args__ = (
        CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="result_progress_range",
        ),
        CheckConstraint("finished_at >= started_at", name="result_timestamp_order"),
        CheckConstraint(
            "retry_after_seconds IS NULL OR retry_after_seconds > 0",
            name="result_retry_positive",
        ),
    )

    command_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("stage_commands.command_id", ondelete="CASCADE"),
        primary_key=True,
    )
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("production_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    progress_percent: Mapped[float] = mapped_column(nullable=False)
    output_artifact_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_after_seconds: Mapped[float | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
