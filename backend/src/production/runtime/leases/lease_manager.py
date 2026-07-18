"""Operational lease policy independent of SQLAlchemy."""

from collections.abc import Callable, Collection
from datetime import datetime, timedelta
from uuid import UUID

from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.runtime.leases.lease_repository import LeaseRepository
from backend.src.production.runtime.runtime_models import ProductionLease


class ProductionLeaseError(RuntimeError):
    """Base error for production lease operations."""


class ProductionLeaseOwnershipError(ProductionLeaseError):
    """Raised when a worker no longer owns an active lease."""


class ProductionLeaseManager:
    """Validate lease policy and delegate storage to LeaseRepository."""

    def __init__(
        self,
        repository: LeaseRepository,
        *,
        clock: Callable[[], datetime],
        lease_duration: timedelta,
    ) -> None:
        if lease_duration.total_seconds() <= 0:
            raise ValueError("lease_duration must be positive")
        self._repository = repository
        self._clock = clock
        self._lease_duration = lease_duration

    def acquire_next(
        self,
        *,
        owner_id: str,
        statuses: Collection[ProductionJobStatus],
    ) -> ProductionLease | None:
        self._require_owner(owner_id)
        if not statuses:
            return None
        now = self._aware_now()
        return self._repository.acquire_next(
            owner_id=owner_id,
            statuses=statuses,
            heartbeat_at=now,
            lease_until=now + self._lease_duration,
        )

    def heartbeat(self, *, job_id: UUID, owner_id: str) -> ProductionLease:
        self._require_owner(owner_id)
        now = self._aware_now()
        lease = self._repository.heartbeat(
            job_id=job_id,
            owner_id=owner_id,
            heartbeat_at=now,
            lease_until=now + self._lease_duration,
        )
        if lease is None:
            raise ProductionLeaseOwnershipError(
                f"worker {owner_id!r} does not own active lease for {job_id}"
            )
        return lease

    def release(self, *, job_id: UUID, owner_id: str) -> bool:
        self._require_owner(owner_id)
        return self._repository.release(job_id=job_id, owner_id=owner_id)

    def expired_job_ids(self) -> tuple[UUID, ...]:
        return self._repository.list_expired_job_ids(expired_at=self._aware_now())

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("lease clock must return a timezone-aware datetime")
        return value

    @staticmethod
    def _require_owner(owner_id: str) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
