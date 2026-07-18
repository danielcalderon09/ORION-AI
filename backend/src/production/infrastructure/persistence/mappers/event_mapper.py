"""Explicit mapping for specialized production domain events."""

from datetime import datetime

from pydantic import TypeAdapter, ValidationError

from backend.src.production.application.events.production_events import (
    ProductionEventType,
    ProductionEventUnion,
)
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionRecordIntegrityError,
)
from backend.src.production.infrastructure.persistence.mappers._common import (
    canonical_uuid,
    json_value,
    parse_uuid,
    require_aware,
)
from backend.src.production.infrastructure.persistence.models.production_event_record import (
    ProductionEventRecord,
)

_EVENT_ADAPTER: TypeAdapter[ProductionEventUnion] = TypeAdapter(ProductionEventUnion)
_COMMON_FIELDS = {
    "schema_version",
    "event_id",
    "job_id",
    "event_type",
    "occurred_at",
    "sequence_number",
    "correlation_id",
    "causation_id",
    "metadata",
}


class ProductionEventMapper:
    @staticmethod
    def to_record(event: ProductionEventUnion, *, db_now: datetime) -> ProductionEventRecord:
        require_aware(db_now, field_name="db_now")
        data = event.model_dump(mode="json")
        payload = {key: value for key, value in data.items() if key not in _COMMON_FIELDS}
        return ProductionEventRecord(
            event_id=canonical_uuid(event.event_id),
            job_id=canonical_uuid(event.job_id),
            event_type=event.event_type.value,
            schema_version=event.schema_version,
            occurred_at=event.occurred_at,
            sequence_number=event.sequence_number,
            correlation_id=canonical_uuid(event.correlation_id),
            causation_id=canonical_uuid(event.causation_id) if event.causation_id else None,
            payload=json_value(payload, field_name="event.payload"),
            metadata_json=json_value(event.metadata, field_name="event.metadata"),
            created_at=db_now,
        )

    @staticmethod
    def to_domain(record: ProductionEventRecord) -> ProductionEventUnion:
        try:
            event_type = ProductionEventType(record.event_type)
        except ValueError as exc:
            raise ProductionRecordIntegrityError(
                f"unknown production event type: {record.event_type!r}"
            ) from exc

        data = {
            "schema_version": record.schema_version,
            "event_id": str(parse_uuid(record.event_id, field_name="event_id")),
            "job_id": str(parse_uuid(record.job_id, field_name="job_id")),
            "event_type": event_type.value,
            "occurred_at": require_aware(record.occurred_at, field_name="occurred_at"),
            "sequence_number": record.sequence_number,
            "correlation_id": str(
                parse_uuid(record.correlation_id, field_name="correlation_id")
            ),
            "causation_id": (
                str(parse_uuid(record.causation_id, field_name="causation_id"))
                if record.causation_id
                else None
            ),
            "metadata": json_value(record.metadata_json, field_name="event.metadata"),
            **json_value(record.payload, field_name="event.payload"),
        }
        try:
            return _EVENT_ADAPTER.validate_python(data)
        except (ValidationError, ValueError, TypeError) as exc:
            raise ProductionRecordIntegrityError(
                f"invalid production event record {record.event_id}"
            ) from exc
