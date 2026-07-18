"""Cooperative heartbeat lifecycle for an active worker lease."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID

from backend.src.production.runtime.lease_manager import ProductionLeaseManager


class ProductionHeartbeat:
    """Renew a lease periodically while a single stage is executing."""

    def __init__(self, lease_manager: ProductionLeaseManager, *, interval: timedelta) -> None:
        if interval.total_seconds() <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._lease_manager = lease_manager
        self._interval_seconds = interval.total_seconds()

    def beat(self, *, job_id: UUID, owner_id: str) -> None:
        self._lease_manager.heartbeat(job_id=job_id, owner_id=owner_id)

    @asynccontextmanager
    async def maintain(self, *, job_id: UUID, owner_id: str) -> AsyncIterator[None]:
        stop = asyncio.Event()

        async def renew_loop() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
                except TimeoutError:
                    self.beat(job_id=job_id, owner_id=owner_id)

        task = asyncio.create_task(renew_loop())
        try:
            yield
        finally:
            stop.set()
            await task
