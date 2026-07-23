"""Read-only durable ProductionVisualAssetPlan artifact queries."""

from uuid import UUID

from sqlalchemy import select

from backend.src.production.domain.enums import ArtifactStatus, ArtifactType
from backend.src.production.image_acquisition.ports import (
    ProductionVisualAssetPlanArtifactCandidate,
)
from backend.src.production.infrastructure.persistence.models import ArtifactRecord
from backend.src.production.infrastructure.persistence.session import (
    ProductionSessionFactory,
)


class SQLAlchemyProductionVisualAssetPlanQueryRepository:
    def __init__(self, session_factory: ProductionSessionFactory) -> None:
        self._session_factory = session_factory

    def list_candidates(
        self,
        *,
        job_id: UUID,
    ) -> tuple[ProductionVisualAssetPlanArtifactCandidate, ...]:
        with self._session_factory() as session:
            records = session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.job_id == str(job_id),
                    ArtifactRecord.artifact_type
                    == ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN.value,
                    ArtifactRecord.status == ArtifactStatus.READY.value,
                )
                .order_by(
                    ArtifactRecord.created_at.desc(),
                    ArtifactRecord.artifact_id.desc(),
                )
            )
            return tuple(
                ProductionVisualAssetPlanArtifactCandidate(
                    artifact_id=UUID(record.artifact_id),
                    job_id=UUID(record.job_id),
                    artifact_type=ArtifactType(record.artifact_type),
                    relative_path=record.relative_path,
                    size_bytes=record.size_bytes,
                    sha256=record.sha256,
                    provider=record.provider,
                    model_version=record.model_version,
                    created_at=record.created_at,
                    metadata=record.metadata_json,
                )
                for record in records
            )

    def list_input_artifact_types(
        self,
        *,
        job_id: UUID,
        artifact_ids: tuple[UUID, ...],
    ) -> dict[UUID, ArtifactType]:
        if not artifact_ids:
            return {}
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    ArtifactRecord.artifact_id,
                    ArtifactRecord.artifact_type,
                ).where(
                    ArtifactRecord.job_id == str(job_id),
                    ArtifactRecord.artifact_id.in_(
                        str(artifact_id) for artifact_id in artifact_ids
                    ),
                )
            )
            result: dict[UUID, ArtifactType] = {}
            for artifact_id, artifact_type in rows:
                try:
                    result[UUID(artifact_id)] = ArtifactType(artifact_type)
                except ValueError:
                    continue
            return result
