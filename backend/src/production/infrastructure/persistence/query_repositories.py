"""Short-session SQLAlchemy query repositories for the Production API."""

from uuid import UUID

from sqlalchemy import func, select

from backend.src.production.application.events import ProductionEventUnion
from backend.src.production.application.services.models import (
    ProductionArtifactPage,
    ProductionArtifactView,
    ProductionJobPage,
    ProductionJobView,
)
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.infrastructure.persistence.mappers.artifact_mapper import (
    ArtifactMapper,
)
from backend.src.production.infrastructure.persistence.mappers.event_mapper import (
    ProductionEventMapper,
)
from backend.src.production.infrastructure.persistence.mappers.production_job_mapper import (
    ProductionJobMapper,
)
from backend.src.production.infrastructure.persistence.models import (
    ArtifactRecord,
    ProductionEventRecord,
    ProductionJobRecord,
)
from backend.src.production.infrastructure.persistence.session import ProductionSessionFactory


class SQLAlchemyProductionJobQueryRepository:
    def __init__(self, session_factory: ProductionSessionFactory) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _view(record: ProductionJobRecord) -> ProductionJobView:
        job = ProductionJobMapper.to_domain(record)
        completed_at = job.updated_at if job.status is ProductionJobStatus.COMPLETED else None
        return ProductionJobView(job=job, row_version=record.row_version, completed_at=completed_at)

    def get(self, job_id: UUID) -> ProductionJobView | None:
        with self._session_factory() as session:
            record = session.get(ProductionJobRecord, str(job_id))
            return self._view(record) if record else None

    def get_by_client_request_id(self, request_id: str) -> ProductionJobView | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(ProductionJobRecord).where(
                    ProductionJobRecord.client_request_id == request_id
                )
            )
            return self._view(record) if record else None

    def list(
        self,
        *,
        status: ProductionJobStatus | None,
        stage: ProductionStage | None,
        limit: int,
        offset: int,
    ) -> ProductionJobPage:
        filters = []
        if status is not None:
            filters.append(ProductionJobRecord.status == status.value)
        if stage is not None:
            filters.append(ProductionJobRecord.current_stage == stage.value)
        with self._session_factory() as session:
            total = session.scalar(
                select(func.count()).select_from(ProductionJobRecord).where(*filters)
            ) or 0
            records = list(
                session.scalars(
                    select(ProductionJobRecord)
                    .where(*filters)
                    .order_by(
                        ProductionJobRecord.created_at.desc(),
                        ProductionJobRecord.job_id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
            )
            return ProductionJobPage(
                items=tuple(self._view(record) for record in records),
                total=total,
                limit=limit,
                offset=offset,
            )


class SQLAlchemyProductionEventQueryRepository:
    def __init__(self, session_factory: ProductionSessionFactory) -> None:
        self._session_factory = session_factory

    def list_for_job(self, job_id: UUID) -> tuple[ProductionEventUnion, ...]:
        with self._session_factory() as session:
            records = session.scalars(
                select(ProductionEventRecord)
                .where(ProductionEventRecord.job_id == str(job_id))
                .order_by(ProductionEventRecord.sequence_number)
            )
            return tuple(ProductionEventMapper.to_domain(record) for record in records)

    def latest_for_job(self, job_id: UUID) -> ProductionEventUnion | None:
        events = self.list_for_job(job_id)
        return events[-1] if events else None

    def next_sequence(self, job_id: UUID) -> int:
        with self._session_factory() as session:
            value = session.scalar(
                select(func.max(ProductionEventRecord.sequence_number)).where(
                    ProductionEventRecord.job_id == str(job_id)
                )
            )
            return 0 if value is None else value + 1


class SQLAlchemyProductionArtifactQueryRepository:
    def __init__(self, session_factory: ProductionSessionFactory) -> None:
        self._session_factory = session_factory

    def list_for_job(self, job_id: UUID) -> ProductionArtifactPage:
        with self._session_factory() as session:
            records = session.scalars(
                select(ArtifactRecord)
                .where(ArtifactRecord.job_id == str(job_id))
                .order_by(ArtifactRecord.relative_path, ArtifactRecord.artifact_id)
            )
            return ProductionArtifactPage(
                items=tuple(
                    ProductionArtifactView(
                        artifact=ArtifactMapper.to_domain(record),
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                    )
                    for record in records
                )
            )
