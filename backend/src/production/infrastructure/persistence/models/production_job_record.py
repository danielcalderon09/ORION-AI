"""SQLAlchemy record for durable production jobs."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.production.infrastructure.persistence.models.base import ProductionBase
from backend.src.production.infrastructure.persistence.models.types import UTCDateTime


class ProductionJobRecord(ProductionBase):
    __tablename__ = "production_jobs"
    __table_args__ = (
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint("updated_at >= created_at", name="job_timestamp_order"),
        Index("ix_production_jobs_status_created", "status", "created_at", "job_id"),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    current_stage: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    long_form_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    clip_project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_db_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_db_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __mapper_args__ = {"version_id_col": row_version}
