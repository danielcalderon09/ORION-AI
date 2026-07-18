"""SQLAlchemy record for an exclusive, renewable production job lease."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.src.production.infrastructure.persistence.models.base import ProductionBase
from backend.src.production.infrastructure.persistence.models.types import UTCDateTime


class ProductionLeaseRecord(ProductionBase):
    __tablename__ = "production_leases"
    __table_args__ = (
        CheckConstraint("length(owner_id) > 0", name="lease_owner_required"),
        CheckConstraint("version >= 1", name="lease_version_positive"),
        CheckConstraint("lease_until >= heartbeat_at", name="lease_timestamp_order"),
    )

    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("production_jobs.job_id", ondelete="CASCADE"),
        primary_key=True,
    )
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
