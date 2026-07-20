"""Short-session read-only query used by durable SCRIPTING input reads."""

from uuid import UUID

from sqlalchemy import select

from backend.src.production.domain.enums import ArtifactStatus, ArtifactType
from backend.src.production.infrastructure.persistence.models import ArtifactRecord
from backend.src.production.infrastructure.persistence.session import ProductionSessionFactory
from backend.src.production.scripting.ports import ProductionPlanArtifactCandidate


class SQLAlchemyProductionPlanQueryRepository:
    def __init__(self, session_factory: ProductionSessionFactory) -> None:
        self._session_factory = session_factory

    def list_candidates(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ProductionPlanArtifactCandidate, ...]:
        with self._session_factory() as session:
            records = session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.job_id == str(job_id),
                    ArtifactRecord.artifact_type == ArtifactType.PRODUCTION_PLAN.value,
                    ArtifactRecord.status == ArtifactStatus.READY.value,
                )
                .order_by(ArtifactRecord.created_at.desc(), ArtifactRecord.artifact_id.desc())
            )
            return tuple(
                ProductionPlanArtifactCandidate(
                    artifact_id=UUID(record.artifact_id),
                    job_id=UUID(record.job_id),
                    relative_path=record.relative_path,
                    size_bytes=record.size_bytes,
                    sha256=record.sha256,
                    provider=record.provider,
                    model_version=record.model_version,
                    metadata=dict(record.metadata_json),
                    created_at=record.created_at,
                )
                for record in records
            )
