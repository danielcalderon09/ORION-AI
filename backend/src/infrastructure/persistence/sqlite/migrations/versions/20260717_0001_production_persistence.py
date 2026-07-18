"""Create durable production persistence tables.

Revision ID: 20260717_0001
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_jobs",
        sa.Column("job_id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("current_stage", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("configuration_snapshot", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("long_form_artifact_id", sa.String(36), nullable=True),
        sa.Column("clip_project_id", sa.String(36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_db_at", sa.DateTime(), nullable=False),
        sa.Column("updated_db_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("row_version >= 1", name="ck_production_jobs_row_version_positive"),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_production_jobs_job_timestamp_order",
        ),
    )
    op.create_index(
        "ix_production_jobs_status_created",
        "production_jobs",
        ["status", "created_at", "job_id"],
    )

    op.create_table(
        "production_artifacts",
        sa.Column("artifact_id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("production_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("artifact_type", sa.String(50), nullable=False),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(255), nullable=True),
        sa.Column("model_version", sa.String(255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "job_id",
            "relative_path",
            name="uq_production_artifact_job_path",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_production_artifacts_artifact_size_nonnegative",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0",
            name="ck_production_artifacts_artifact_duration_positive",
        ),
        sa.CheckConstraint(
            "width IS NULL OR width > 0",
            name="ck_production_artifacts_artifact_width_positive",
        ),
        sa.CheckConstraint(
            "height IS NULL OR height > 0",
            name="ck_production_artifacts_artifact_height_positive",
        ),
    )

    op.create_table(
        "stage_commands",
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("production_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("input_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("configuration_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_stage_commands_command_attempt_positive",
        ),
    )

    op.create_table(
        "stage_results",
        sa.Column(
            "command_id",
            sa.String(36),
            sa.ForeignKey("stage_commands.command_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("production_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.Column("progress_percent", sa.Float(), nullable=False),
        sa.Column("output_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_after_seconds", sa.Float(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="ck_stage_results_result_progress_range",
        ),
        sa.CheckConstraint(
            "finished_at >= started_at",
            name="ck_stage_results_result_timestamp_order",
        ),
        sa.CheckConstraint(
            "retry_after_seconds IS NULL OR retry_after_seconds > 0",
            name="ck_stage_results_result_retry_positive",
        ),
    )

    op.create_table(
        "production_stage_runs",
        sa.Column("stage_run_id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("production_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column(
            "command_id",
            sa.String(36),
            sa.ForeignKey("stage_commands.command_id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "result_id",
            sa.String(36),
            sa.ForeignKey("stage_results.command_id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "job_id",
            "stage",
            "attempt_number",
            name="uq_production_stage_run_attempt",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_production_stage_runs_stage_run_attempt_positive",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_production_stage_runs_stage_run_timestamp_order",
        ),
    )

    op.create_table(
        "production_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("production_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("causation_id", sa.String(36), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "job_id",
            "sequence_number",
            name="uq_production_event_job_sequence",
        ),
        sa.CheckConstraint(
            "sequence_number >= 0",
            name="ck_production_events_sequence_nonnegative",
        ),
    )


def downgrade() -> None:
    op.drop_table("production_events")
    op.drop_table("production_stage_runs")
    op.drop_table("stage_results")
    op.drop_table("stage_commands")
    op.drop_table("production_artifacts")
    op.drop_index("ix_production_jobs_status_created", table_name="production_jobs")
    op.drop_table("production_jobs")
