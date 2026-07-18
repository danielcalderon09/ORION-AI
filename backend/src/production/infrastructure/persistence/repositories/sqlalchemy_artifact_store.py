"""SQLAlchemy implementation of ArtifactStorePort."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.src.production.domain.artifact import Artifact
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionIdempotencyConflictError,
    ProductionRecordIntegrityError,
)
from backend.src.production.infrastructure.persistence.mappers.artifact_mapper import ArtifactMapper
from backend.src.production.infrastructure.persistence.models.artifact_record import ArtifactRecord


class SQLAlchemyArtifactStore:
    """Register immutable artifact contracts in a caller-owned transaction."""

    def __init__(self, session: Session, *, clock: Callable[[], datetime]) -> None:
        self._session = session
        self._clock = clock

    async def save(self, artifact: Artifact) -> Artifact:
        existing = self._session.get(ArtifactRecord, str(artifact.artifact_id))
        if existing is not None:
            persisted = ArtifactMapper.to_domain(existing)
            if persisted.job_id != artifact.job_id:
                raise ProductionIdempotencyConflictError(
                    f"artifact {artifact.artifact_id} cannot change job"
                )
            if persisted != artifact:
                raise ProductionIdempotencyConflictError(
                    f"artifact {artifact.artifact_id} has different content"
                )
            return persisted

        path_statement = select(ArtifactRecord).where(
            ArtifactRecord.job_id == str(artifact.job_id),
            ArtifactRecord.relative_path == artifact.relative_path,
        )
        path_owner = self._session.scalar(path_statement)
        if path_owner is not None:
            raise ProductionIdempotencyConflictError(
                f"artifact path already belongs to {path_owner.artifact_id}"
            )

        record = ArtifactMapper.to_record(artifact, db_now=self._clock())
        self._session.add(record)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ProductionRecordIntegrityError(
                f"could not save artifact {artifact.artifact_id}"
            ) from exc
        return ArtifactMapper.to_domain(record)

    async def get(self, artifact_id: UUID) -> Artifact | None:
        record = self._session.get(ArtifactRecord, str(artifact_id))
        return ArtifactMapper.to_domain(record) if record else None

    async def list_for_job(self, job_id: UUID) -> list[Artifact]:
        statement = (
            select(ArtifactRecord)
            .where(ArtifactRecord.job_id == str(job_id))
            .order_by(ArtifactRecord.relative_path, ArtifactRecord.artifact_id)
        )
        return [ArtifactMapper.to_domain(record) for record in self._session.scalars(statement)]
