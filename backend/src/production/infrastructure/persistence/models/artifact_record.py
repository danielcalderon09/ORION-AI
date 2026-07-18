"""SQLAlchemy record for production artifact metadata."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.production.infrastructure.persistence.models.base import ProductionBase
from backend.src.production.infrastructure.persistence.models.types import UTCDateTime


class ArtifactRecord(ProductionBase):
    __tablename__ = "production_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "relative_path",
            name="uq_production_artifact_job_path",
        ),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="artifact_size_nonnegative"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0",
            name="artifact_duration_positive",
        ),
        CheckConstraint("width IS NULL OR width > 0", name="artifact_width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="artifact_height_positive"),
    )

    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("production_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    width: Mapped[int | None] = mapped_column(nullable=True)
    height: Mapped[int | None] = mapped_column(nullable=True)
    provider: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
