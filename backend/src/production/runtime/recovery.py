"""Independent recovery of due retries and inspection of expired leases."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from backend.src.production.application.events import ProductionJobQueued
from backend.src.production.application.orchestration import (
    OrchestrationDecision,
    TransitionPolicy,
)
from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionConcurrencyError,
)
from backend.src.production.runtime.blocking_executor import RuntimeBlockingExecutor
from backend.src.production.runtime.decision_persister import RuntimeDecisionPersister
from backend.src.production.runtime.leases import ProductionLeaseManager
from backend.src.production.runtime.runtime_models import RuntimeRecoveryResult
from backend.src.production.runtime.runtime_state_reader import RuntimeStateReader


class ProductionRecoveryService:
    """Recover durable retry state without claiming jobs or executing handlers."""

    def __init__(
        self,
        state_reader: RuntimeStateReader,
        decision_store: RuntimeDecisionPersister,
        lease_manager: ProductionLeaseManager,
        blocking_executor: RuntimeBlockingExecutor,
        *,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
    ) -> None:
        self._state_reader = state_reader
        self._decision_store = decision_store
        self._lease_manager = lease_manager
        self._blocking_executor = blocking_executor
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def recover(self) -> RuntimeRecoveryResult:
        return RuntimeRecoveryResult(
            requeued_job_ids=await self.requeue_due_retries(),
            expired_lease_job_ids=self.inspect_expired_leases(),
        )

    def inspect_expired_leases(self) -> tuple[UUID, ...]:
        return self._lease_manager.expired_job_ids()

    async def requeue_due_retries(self) -> tuple[UUID, ...]:
        now = self._clock()
        candidates = await self._blocking_executor.run(
            self._state_reader.list_retry_candidates
        )
        requeued: list[UUID] = []
        for candidate in candidates:
            job = candidate.job
            retry_event = candidate.retry_event
            if retry_event.retry_at > now:
                continue
            TransitionPolicy.validate_transition(job.status, ProductionJobStatus.QUEUED)
            updated = job.model_copy(
                update={
                    "status": ProductionJobStatus.QUEUED,
                    "updated_at": now,
                    "error_code": None,
                    "error_message": None,
                }
            )
            queued = ProductionJobQueued(
                event_id=self._uuid_factory(),
                job_id=job.job_id,
                occurred_at=now,
                sequence_number=candidate.next_sequence_number,
                correlation_id=job.job_id,
                causation_id=retry_event.event_id,
                metadata={"recovery": "retry_due"},
            )
            try:
                await self._decision_store.persist_decision(
                    previous_job=job,
                    decision=OrchestrationDecision(
                        updated_job=updated,
                        events=(queued,),
                        should_continue=True,
                        reason="retry_due",
                    ),
                )
            except ProductionConcurrencyError:
                # Expected race: another process durably requeued the same job first.
                continue
            requeued.append(job.job_id)
        return tuple(requeued)
