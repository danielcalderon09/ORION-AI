"""Explicit mapping between ProductionJob and ProductionJobRecord."""

from datetime import datetime
from uuid import UUID

from pydantic import ValidationError

from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionRecordIntegrityError,
)
from backend.src.production.infrastructure.persistence.mappers._common import (
    canonical_uuid,
    json_value,
    parse_uuid,
    require_aware,
)
from backend.src.production.infrastructure.persistence.models.production_job_record import (
    ProductionJobRecord,
)


class ProductionJobMapper:
    @staticmethod
    def to_record(
        job: ProductionJob,
        *,
        db_now: datetime,
        row_version: int = 1,
    ) -> ProductionJobRecord:
        require_aware(db_now, field_name="db_now")
        return ProductionJobRecord(
            job_id=canonical_uuid(job.job_id),
            schema_version=job.schema_version,
            prompt=job.prompt,
            status=job.status.value,
            current_stage=job.current_stage.value,
            created_at=job.created_at,
            updated_at=job.updated_at,
            configuration_snapshot=json_value(
                job.configuration_snapshot,
                field_name="configuration_snapshot",
            ),
            error_code=job.error_code,
            error_message=job.error_message,
            long_form_artifact_id=(
                canonical_uuid(job.long_form_artifact_id) if job.long_form_artifact_id else None
            ),
            clip_project_id=canonical_uuid(job.clip_project_id) if job.clip_project_id else None,
            client_request_id=job.client_request_id,
            request_fingerprint=job.request_fingerprint,
            row_version=row_version,
            created_db_at=db_now,
            updated_db_at=db_now,
        )

    @staticmethod
    def update_record(
        record: ProductionJobRecord,
        job: ProductionJob,
        *,
        db_now: datetime,
    ) -> None:
        if record.job_id != canonical_uuid(job.job_id):
            raise ProductionRecordIntegrityError("cannot change ProductionJobRecord job_id")
        require_aware(db_now, field_name="db_now")
        record.schema_version = job.schema_version
        record.prompt = job.prompt
        record.status = job.status.value
        record.current_stage = job.current_stage.value
        record.created_at = job.created_at
        record.updated_at = job.updated_at
        record.configuration_snapshot = json_value(
            job.configuration_snapshot,
            field_name="configuration_snapshot",
        )
        record.error_code = job.error_code
        record.error_message = job.error_message
        record.long_form_artifact_id = (
            canonical_uuid(job.long_form_artifact_id) if job.long_form_artifact_id else None
        )
        record.clip_project_id = canonical_uuid(job.clip_project_id) if job.clip_project_id else None
        record.client_request_id = job.client_request_id
        record.request_fingerprint = job.request_fingerprint
        record.updated_db_at = db_now

    @staticmethod
    def to_domain(record: ProductionJobRecord) -> ProductionJob:
        try:
            return ProductionJob(
                schema_version=record.schema_version,
                job_id=parse_uuid(record.job_id, field_name="job_id"),
                prompt=record.prompt,
                status=ProductionJobStatus(record.status),
                current_stage=ProductionStage(record.current_stage),
                created_at=require_aware(record.created_at, field_name="created_at"),
                updated_at=require_aware(record.updated_at, field_name="updated_at"),
                configuration_snapshot=json_value(
                    record.configuration_snapshot,
                    field_name="configuration_snapshot",
                ),
                error_code=record.error_code,
                error_message=record.error_message,
                long_form_artifact_id=(
                    UUID(record.long_form_artifact_id) if record.long_form_artifact_id else None
                ),
                clip_project_id=UUID(record.clip_project_id) if record.clip_project_id else None,
                client_request_id=record.client_request_id,
                request_fingerprint=record.request_fingerprint,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise ProductionRecordIntegrityError(
                f"invalid production job record {record.job_id}"
            ) from exc
