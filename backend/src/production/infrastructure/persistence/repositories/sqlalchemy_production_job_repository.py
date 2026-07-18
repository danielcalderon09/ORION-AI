"""SQLAlchemy implementation of ProductionJobRepositoryPort."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionConcurrencyError,
    ProductionIdempotencyConflictError,
    ProductionRecordIntegrityError,
)
from backend.src.production.infrastructure.persistence.mappers.production_job_mapper import (
    ProductionJobMapper,
)
from backend.src.production.infrastructure.persistence.models.production_job_record import (
    ProductionJobRecord,
)


class SQLAlchemyProductionJobRepository:
    """Use one caller-owned session and flush changes without committing."""

    def __init__(self, session: Session, *, clock: Callable[[], datetime]) -> None:
        self._session = session
        self._clock = clock
        self._tracked_records: dict[UUID, ProductionJobRecord] = {}

    async def add(self, job: ProductionJob) -> ProductionJob:
        if self._session.get(ProductionJobRecord, str(job.job_id)) is not None:
            raise ProductionIdempotencyConflictError(f"job already exists: {job.job_id}")
        record = ProductionJobMapper.to_record(job, db_now=self._clock())
        self._session.add(record)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ProductionRecordIntegrityError(f"could not add job {job.job_id}") from exc
        self._tracked_records[job.job_id] = record
        return ProductionJobMapper.to_domain(record)

    async def save(self, job: ProductionJob) -> ProductionJob:
        record = self._tracked_records.get(job.job_id)
        if record is None:
            record = self._session.get(ProductionJobRecord, str(job.job_id))
            if record is None:
                raise ProductionRecordIntegrityError(f"job does not exist: {job.job_id}")
            self._tracked_records[job.job_id] = record
        ProductionJobMapper.update_record(record, job, db_now=self._clock())
        try:
            self._session.flush()
        except StaleDataError as exc:
            raise ProductionConcurrencyError(f"stale production job: {job.job_id}") from exc
        except IntegrityError as exc:
            raise ProductionRecordIntegrityError(f"could not save job {job.job_id}") from exc
        return ProductionJobMapper.to_domain(record)

    async def get(self, job_id: UUID) -> ProductionJob | None:
        record = self._session.get(ProductionJobRecord, str(job_id))
        if record is None:
            return None
        self._tracked_records[job_id] = record
        return ProductionJobMapper.to_domain(record)

    async def list_by_status(
        self,
        statuses: set[ProductionJobStatus],
        limit: int = 100,
    ) -> list[ProductionJob]:
        if limit < 1:
            return []
        if not statuses:
            return []
        statement = (
            select(ProductionJobRecord)
            .where(ProductionJobRecord.status.in_(status.value for status in statuses))
            .order_by(ProductionJobRecord.created_at, ProductionJobRecord.job_id)
            .limit(limit)
        )
        records = list(self._session.scalars(statement))
        for record in records:
            self._tracked_records[UUID(record.job_id)] = record
        return [ProductionJobMapper.to_domain(record) for record in records]

    def row_version(self, job_id: UUID) -> int | None:
        record = self._tracked_records.get(job_id)
        if record is None:
            record = self._session.get(ProductionJobRecord, str(job_id))
        return record.row_version if record else None
