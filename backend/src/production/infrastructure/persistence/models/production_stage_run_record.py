"""SQLAlchemy record for the lifecycle of one stage attempt."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.production.infrastructure.persistence.models.base import ProductionBase
from backend.src.production.infrastructure.persistence.models.types import UTCDateTime


class ProductionStageRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_TRANSIENT = "failed_transient"
    FAILED_PERMANENT = "failed_permanent"
    NEEDS_USER_ACTION = "needs_user_action"
    CANCELLED = "cancelled"


class ProductionStageRunRecord(ProductionBase):
    __tablename__ = "production_stage_runs"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "stage",
            "attempt_number",
            name="uq_production_stage_run_attempt",
        ),
        CheckConstraint("attempt_number >= 1", name="stage_run_attempt_positive"),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="stage_run_timestamp_order",
        ),
    )

    stage_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("production_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    attempt_number: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    command_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("stage_commands.command_id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    result_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("stage_results.command_id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
