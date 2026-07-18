"""Durable production-job repository boundary."""

from typing import Protocol
from uuid import UUID

from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.domain.production_job import ProductionJob


class ProductionJobRepositoryPort(Protocol):
    """Persist and query production jobs independently of a database."""

    async def add(self, job: ProductionJob) -> ProductionJob:
        """Add a new job, rejecting an existing ID."""
        ...

    async def save(self, job: ProductionJob) -> ProductionJob:
        """Persist the current aggregate state."""
        ...

    async def get(self, job_id: UUID) -> ProductionJob | None:
        """Return a job by ID, if present."""
        ...

    async def list_by_status(
        self,
        statuses: set[ProductionJobStatus],
        limit: int = 100,
    ) -> list[ProductionJob]:
        """Return recoverable jobs in deterministic creation order."""
        ...
