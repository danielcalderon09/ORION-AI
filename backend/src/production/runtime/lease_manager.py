"""Atomic SQLite-backed job leasing for local production workers."""

from collections.abc import Callable, Collection
from datetime import datetime, timedelta
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


class ProductionLeaseError(RuntimeError):
    """Base error for production lease operations."""


class ProductionLeaseOwnershipError(ProductionLeaseError):
    """Raised when a worker no longer owns an active lease."""


class ProductionLeaseManager:
    """Claim, renew, and release one durable lease per production job."""

    def __init__(
        self,
        session_factory: ProductionSessionFactory,
        *,
        clock: Callable[[], datetime],
        lease_duration: timedelta,
    ) -> None:
        if lease_duration.total_seconds() <= 0:
            raise ValueError("lease_duration must be positive")
        self._session_factory = session_factory
        self._clock = clock
        self._lease_duration = lease_duration

    def acquire_next(
        self,
        *,
        owner_id: str,
        statuses: Collection[ProductionJobStatus],
    ) -> ProductionLease | None:
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if not statuses:
            return None
        now = self._clock()
        self._require_aware(now)
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
                            ProductionLeaseRecord.lease_until <= now,
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
                    heartbeat_at=now,
                    lease_until=now + self._lease_duration,
                    version=1,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[ProductionLeaseRecord.job_id],
                    set_={
                        "owner_id": owner_id,
                        "heartbeat_at": now,
                        "lease_until": now + self._lease_duration,
                        "version": ProductionLeaseRecord.version + 1,
                    },
                    where=or_(
                        ProductionLeaseRecord.lease_until <= now,
                        ProductionLeaseRecord.owner_id == owner_id,
                    ),
                )
                result = session.execute(statement)
                rowcount = cast(int, getattr(result, "rowcount", 0))
                if rowcount != 1:
                    session.rollback()
                    continue
                session.commit()
                record = session.get(ProductionLeaseRecord, job_id)
                if record is None:
                    raise ProductionLeaseError("acquired lease disappeared")
                return self._to_domain(record)
        return None

    def heartbeat(self, *, job_id: UUID, owner_id: str) -> ProductionLease:
        now = self._clock()
        self._require_aware(now)
        with self._session_factory() as session:
            result = session.execute(
                update(ProductionLeaseRecord)
                .where(
                    ProductionLeaseRecord.job_id == str(job_id),
                    ProductionLeaseRecord.owner_id == owner_id,
                    ProductionLeaseRecord.lease_until > now,
                )
                .values(
                    heartbeat_at=now,
                    lease_until=now + self._lease_duration,
                    version=ProductionLeaseRecord.version + 1,
                )
            )
            rowcount = cast(int, getattr(result, "rowcount", 0))
            if rowcount != 1:
                session.rollback()
                raise ProductionLeaseOwnershipError(
                    f"worker {owner_id!r} does not own active lease for {job_id}"
                )
            session.commit()
            record = session.get(ProductionLeaseRecord, str(job_id))
            if record is None:
                raise ProductionLeaseError("renewed lease disappeared")
            return self._to_domain(record)

    def release(self, *, job_id: UUID, owner_id: str) -> bool:
        with self._session_factory() as session:
            result = session.execute(
                delete(ProductionLeaseRecord).where(
                    ProductionLeaseRecord.job_id == str(job_id),
                    ProductionLeaseRecord.owner_id == owner_id,
                )
            )
            session.commit()
            rowcount = cast(int, getattr(result, "rowcount", 0))
            return rowcount == 1

    def expired_job_ids(self) -> tuple[UUID, ...]:
        now = self._clock()
        with self._session_factory() as session:
            ids = session.scalars(
                select(ProductionLeaseRecord.job_id)
                .where(ProductionLeaseRecord.lease_until <= now)
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

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("lease clock must return a timezone-aware datetime")
