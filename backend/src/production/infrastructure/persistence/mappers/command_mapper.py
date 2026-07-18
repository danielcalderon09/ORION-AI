"""Explicit mapping between StageCommand and StageCommandRecord."""

from pydantic import ValidationError

from backend.src.production.application.commands.stage_command import StageCommand
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
from backend.src.production.infrastructure.persistence.models.stage_command_record import (
    StageCommandRecord,
)


class StageCommandMapper:
    @staticmethod
    def to_record(command: StageCommand) -> StageCommandRecord:
        return StageCommandRecord(
            command_id=canonical_uuid(command.command_id),
            job_id=canonical_uuid(command.job_id),
            schema_version=command.schema_version,
            stage=command.stage.value,
            attempt_number=command.attempt_number,
            idempotency_key=command.idempotency_key,
            input_artifact_ids=[canonical_uuid(item) for item in command.input_artifact_ids],
            configuration_snapshot=json_value(
                command.configuration_snapshot,
                field_name="command.configuration_snapshot",
            ),
            created_at=command.created_at,
            processed_at=None,
        )

    @staticmethod
    def to_domain(record: StageCommandRecord) -> StageCommand:
        try:
            return StageCommand(
                schema_version=record.schema_version,
                command_id=parse_uuid(record.command_id, field_name="command_id"),
                job_id=parse_uuid(record.job_id, field_name="job_id"),
                stage=ProductionStage(record.stage),
                attempt_number=record.attempt_number,
                idempotency_key=record.idempotency_key,
                input_artifact_ids=tuple(
                    parse_uuid(item, field_name="input_artifact_ids")
                    for item in record.input_artifact_ids
                ),
                configuration_snapshot=json_value(
                    record.configuration_snapshot,
                    field_name="command.configuration_snapshot",
                ),
                created_at=require_aware(record.created_at, field_name="created_at"),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise ProductionRecordIntegrityError(
                f"invalid stage command record {record.command_id}"
            ) from exc
