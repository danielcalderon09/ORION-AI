"""SQLite/SQLAlchemy implementation of the internal lease repository."""

from collections.abc import Collection
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.infrastructure.persistence.models import (
    ProductionJobRecord,
    ProductionLeaseRecord,
)
from backend.src.production.infrastructure.persistence.session import ProductionSessionFactory
from backend.src.production.runtime.runtime_models import ProductionLease


class SQLAlchemyLeaseRepository:
    """Own sessions and commits for short, isolated lease transactions."""

    def __init__(self, session_factory: ProductionSessionFactory) -> None:
        self._session_factory = session_factory

    def acquire_next(
        self,
        *,
        owner_id: str,
        statuses: Collection[ProductionJobStatus],
        heartbeat_at: datetime,
        lease_until: datetime,
    ) -> ProductionLease | None:
        with self._session_factory() as session:
            candidates = list(
                session.scalars(
                    select(ProductionJobRecord.job_id)
                    .outerjoin(
                        ProductionLeaseRecord,
                        ProductionLeaseRecord.job_id == ProductionJobRecord.job_id,
                    )
                    .where(
                        ProductionJobRecord.status.in_(status.value for status in statuses),
                        or_(
                            ProductionLeaseRecord.job_id.is_(None),
                            ProductionLeaseRecord.lease_until <= heartbeat_at,
                            ProductionLeaseRecord.owner_id == owner_id,
                        ),
                    )
                    .order_by(ProductionJobRecord.created_at, ProductionJobRecord.job_id)
                )
            )
            for job_id in candidates:
                statement = sqlite_insert(ProductionLeaseRecord).values(
                    job_id=job_id,
                    owner_id=owner_id,
                    heartbeat_at=heartbeat_at,
                    lease_until=lease_until,
                    version=1,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[ProductionLeaseRecord.job_id],
                    set_={
                        "owner_id": owner_id,
                        "heartbeat_at": heartbeat_at,
                        "lease_until": lease_until,
                        "version": ProductionLeaseRecord.version + 1,
                    },
                    where=or_(
                        ProductionLeaseRecord.lease_until <= heartbeat_at,
                        ProductionLeaseRecord.owner_id == owner_id,
                    ),
                )
                result = session.execute(statement)
                if cast(int, getattr(result, "rowcount", 0)) != 1:
                    session.rollback()
                    continue
                session.commit()
                record = session.get(ProductionLeaseRecord, job_id)
                if record is None:
                    raise RuntimeError("acquired lease disappeared")
                return self._to_domain(record)
        return None

    def heartbeat(
        self,
        *,
        job_id: UUID,
        owner_id: str,
        heartbeat_at: datetime,
        lease_until: datetime,
    ) -> ProductionLease | None:
        with self._session_factory() as session:
            result = session.execute(
                update(ProductionLeaseRecord)
                .where(
                    ProductionLeaseRecord.job_id == str(job_id),
                    ProductionLeaseRecord.owner_id == owner_id,
                    ProductionLeaseRecord.lease_until > heartbeat_at,
                )
                .values(
                    heartbeat_at=heartbeat_at,
                    lease_until=lease_until,
                    version=ProductionLeaseRecord.version + 1,
                )
            )
            if cast(int, getattr(result, "rowcount", 0)) != 1:
                session.rollback()
                return None
            session.commit()
            record = session.get(ProductionLeaseRecord, str(job_id))
            return self._to_domain(record) if record is not None else None

    def release(self, *, job_id: UUID, owner_id: str) -> bool:
        with self._session_factory() as session:
            result = session.execute(
                delete(ProductionLeaseRecord).where(
                    ProductionLeaseRecord.job_id == str(job_id),
                    ProductionLeaseRecord.owner_id == owner_id,
                )
            )
            session.commit()
            return cast(int, getattr(result, "rowcount", 0)) == 1

    def list_expired_job_ids(self, *, expired_at: datetime) -> tuple[UUID, ...]:
        with self._session_factory() as session:
            ids = session.scalars(
                select(ProductionLeaseRecord.job_id)
                .where(ProductionLeaseRecord.lease_until <= expired_at)
                .order_by(ProductionLeaseRecord.lease_until, ProductionLeaseRecord.job_id)
            )
            return tuple(UUID(item) for item in ids)

    @staticmethod
    def _to_domain(record: ProductionLeaseRecord) -> ProductionLease:
        return ProductionLease(
            job_id=UUID(record.job_id),
            owner_id=record.owner_id,
            lease_until=record.lease_until,
            heartbeat_at=record.heartbeat_at,
            version=record.version,
        )
