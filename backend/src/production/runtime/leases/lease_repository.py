"""Internal persistence port for exclusive production leases."""

from collections.abc import Collection
from datetime import datetime
from typing import Protocol
from uuid import UUID

from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.runtime.runtime_models import ProductionLease


class LeaseRepository(Protocol):
    def acquire_next(
        self,
        *,
        owner_id: str,
        statuses: Collection[ProductionJobStatus],
        heartbeat_at: datetime,
        lease_until: datetime,
    ) -> ProductionLease | None: ...

    def heartbeat(
        self,
        *,
        job_id: UUID,
        owner_id: str,
        heartbeat_at: datetime,
        lease_until: datetime,
    ) -> ProductionLease | None: ...

    def release(self, *, job_id: UUID, owner_id: str) -> bool: ...

    def list_expired_job_ids(self, *, expired_at: datetime) -> tuple[UUID, ...]: ...
