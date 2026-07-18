"""Add exclusive leases for the local production runtime.

Revision ID: 20260717_0002
Revises: 20260717_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0002"
down_revision: str | None = "20260717_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_leases",
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("production_jobs.job_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(owner_id) > 0", name="ck_production_leases_owner_required"),
        sa.CheckConstraint("version >= 1", name="ck_production_leases_version_positive"),
        sa.CheckConstraint(
            "lease_until >= heartbeat_at",
            name="ck_production_leases_timestamp_order",
        ),
    )
    op.create_index(
        "ix_production_leases_expiration",
        "production_leases",
        ["lease_until", "job_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_production_leases_expiration", table_name="production_leases")
    op.drop_table("production_leases")
