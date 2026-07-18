"""Small facade for claim, delegation, release, and loop policy."""

import asyncio

from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.runtime.claimed_job_processor import (
    ClaimedJobProcessor,
    ProductionRuntimeError,
)
from backend.src.production.runtime.leases import ProductionLeaseManager
from backend.src.production.runtime.recovery import ProductionRecoveryService
from backend.src.production.runtime.runtime_models import WorkerRunResult
from backend.src.production.runtime.worker_loop import (
    ProductionWorkerLoop,
    ProductionWorkerLoopError,
)


class ProductionWorker:
    """Claim work, delegate one durable decision, and always release its lease."""

    CLAIMABLE_STATUSES = frozenset(
        {
            ProductionJobStatus.QUEUED,
            ProductionJobStatus.RUNNING,
            ProductionJobStatus.CANCEL_REQUESTED,
        }
    )

    def __init__(
        self,
        *,
        owner_id: str,
        lease_manager: ProductionLeaseManager,
        recovery: ProductionRecoveryService,
        processor: ClaimedJobProcessor,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        self._owner_id = owner_id
        self._lease_manager = lease_manager
        self._recovery = recovery
        self._processor = processor
        self._loop = ProductionWorkerLoop(self.run_once)

    async def run_once(self) -> WorkerRunResult:
        await self._recovery.requeue_due_retries()
        lease = self._lease_manager.acquire_next(
            owner_id=self._owner_id,
            statuses=self.CLAIMABLE_STATUSES,
        )
        if lease is None:
            return WorkerRunResult(processed=False, reason="no_claimable_job")
        try:
            return await self._processor.process(lease=lease, owner_id=self._owner_id)
        finally:
            self._lease_manager.release(job_id=lease.job_id, owner_id=self._owner_id)

    async def run_until_idle(self, *, max_cycles: int = 100) -> tuple[WorkerRunResult, ...]:
        try:
            return await self._loop.run_until_idle(max_cycles=max_cycles)
        except ProductionWorkerLoopError as exc:
            raise ProductionRuntimeError(str(exc)) from exc

    async def run_forever(
        self,
        *,
        stop_event: asyncio.Event,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        await self._loop.run_forever(
            stop_event=stop_event,
            poll_interval_seconds=poll_interval_seconds,
        )


__all__ = ["ProductionRuntimeError", "ProductionWorker"]
