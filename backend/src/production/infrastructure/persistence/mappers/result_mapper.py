"""Explicit mapping between StageResult and StageResultRecord."""

from datetime import datetime

from pydantic import ValidationError

from backend.src.production.application.results.stage_result import StageOutcome, StageResult
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionRecordIntegrityError,
)
from backend.src.production.infrastructure.persistence.mappers._common import (
    canonical_uuid,
    json_value,
    parse_uuid,
    require_aware,
)
from backend.src.production.infrastructure.persistence.models.stage_result_record import (
    StageResultRecord,
)


class StageResultMapper:
    @staticmethod
    def to_record(result: StageResult, *, db_now: datetime) -> StageResultRecord:
        require_aware(db_now, field_name="db_now")
        return StageResultRecord(
            command_id=canonical_uuid(result.command_id),
            job_id=canonical_uuid(result.job_id),
            schema_version=result.schema_version,
            stage=result.stage.value,
            outcome=result.outcome.value,
            started_at=result.started_at,
            finished_at=result.finished_at,
            progress_percent=result.progress_percent,
            output_artifact_ids=[canonical_uuid(item) for item in result.output_artifact_ids],
            error_code=result.error_code,
            error_message=result.error_message,
            retry_after_seconds=result.retry_after_seconds,
            metadata_json=json_value(result.metadata, field_name="result.metadata"),
            created_at=db_now,
        )

    @staticmethod
    def to_domain(record: StageResultRecord) -> StageResult:
        try:
            return StageResult(
                schema_version=record.schema_version,
                command_id=parse_uuid(record.command_id, field_name="command_id"),
                job_id=parse_uuid(record.job_id, field_name="job_id"),
                stage=ProductionStage(record.stage),
                outcome=StageOutcome(record.outcome),
                started_at=require_aware(record.started_at, field_name="started_at"),
                finished_at=require_aware(record.finished_at, field_name="finished_at"),
                progress_percent=record.progress_percent,
                output_artifact_ids=tuple(
                    parse_uuid(item, field_name="output_artifact_ids")
                    for item in record.output_artifact_ids
                ),
                error_code=record.error_code,
                error_message=record.error_message,
                retry_after_seconds=record.retry_after_seconds,
                metadata=json_value(record.metadata_json, field_name="result.metadata"),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise ProductionRecordIntegrityError(
                f"invalid stage result record {record.command_id}"
            ) from exc
