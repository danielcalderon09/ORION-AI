"""Add durable HTTP request idempotency to production jobs.

Revision ID: 20260718_0003
Revises: 20260717_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0003"
down_revision: str | None = "20260717_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("production_jobs") as batch:
        batch.add_column(sa.Column("client_request_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("request_fingerprint", sa.String(64), nullable=True))
        batch.create_unique_constraint(
            "uq_production_jobs_client_request_id",
            ["client_request_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("production_jobs") as batch:
        batch.drop_constraint(
            "uq_production_jobs_client_request_id",
            type_="unique",
        )
        batch.drop_column("request_fingerprint")
        batch.drop_column("client_request_id")
