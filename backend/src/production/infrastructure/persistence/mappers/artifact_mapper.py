"""Explicit mapping between Artifact and ArtifactRecord."""

from datetime import datetime

from pydantic import ValidationError

from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionRecordIntegrityError,
)
from backend.src.production.infrastructure.persistence.mappers._common import (
    canonical_uuid,
    json_value,
    parse_uuid,
    require_aware,
)
from backend.src.production.infrastructure.persistence.models.artifact_record import ArtifactRecord


class ArtifactMapper:
    @staticmethod
    def to_record(artifact: Artifact, *, db_now: datetime) -> ArtifactRecord:
        require_aware(db_now, field_name="db_now")
        return ArtifactRecord(
            artifact_id=canonical_uuid(artifact.artifact_id),
            job_id=canonical_uuid(artifact.job_id),
            schema_version=artifact.schema_version,
            artifact_type=artifact.artifact_type.value,
            relative_path=artifact.relative_path,
            mime_type=artifact.mime_type,
            status=artifact.status.value,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            duration_seconds=artifact.duration_seconds,
            width=artifact.width,
            height=artifact.height,
            provider=artifact.provider,
            model_version=artifact.model_version,
            metadata_json=json_value(artifact.metadata, field_name="artifact.metadata"),
            created_at=db_now,
            updated_at=db_now,
        )

    @staticmethod
    def to_domain(record: ArtifactRecord) -> Artifact:
        try:
            return Artifact(
                schema_version=record.schema_version,
                artifact_id=parse_uuid(record.artifact_id, field_name="artifact_id"),
                job_id=parse_uuid(record.job_id, field_name="job_id"),
                artifact_type=ArtifactType(record.artifact_type),
                relative_path=record.relative_path,
                mime_type=record.mime_type,
                status=ArtifactStatus(record.status),
                size_bytes=record.size_bytes,
                sha256=record.sha256,
                duration_seconds=record.duration_seconds,
                width=record.width,
                height=record.height,
                provider=record.provider,
                model_version=record.model_version,
                metadata=json_value(record.metadata_json, field_name="artifact.metadata"),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise ProductionRecordIntegrityError(
                f"invalid artifact record {record.artifact_id}"
            ) from exc
