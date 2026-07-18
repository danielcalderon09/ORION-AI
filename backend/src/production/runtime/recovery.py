"""Deterministic recovery of due retries and expired leases."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from backend.src.production.application.events import (
    ProductionEventType,
    ProductionJobQueued,
    ProductionRetryScheduled,
)
from backend.src.production.application.orchestration import (
    OrchestrationDecision,
    TransitionPolicy,
)
from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionConcurrencyError,
)
from backend.src.production.infrastructure.persistence.mappers.event_mapper import (
    ProductionEventMapper,
)
from backend.src.production.infrastructure.persistence.mappers.production_job_mapper import (
    ProductionJobMapper,
)
from backend.src.production.infrastructure.persistence.models import (
    ProductionEventRecord,
    ProductionJobRecord,
)
from backend.src.production.infrastructure.persistence.session import ProductionSessionFactory
from backend.src.production.infrastructure.persistence.transactions import (
    OrchestrationDecisionStore,
)
from backend.src.production.runtime.lease_manager import ProductionLeaseManager
from backend.src.production.runtime.runtime_models import RuntimeRecoveryResult


class ProductionRecoveryService:
    """Move due retries to QUEUED; expired leases remain safely reclaimable."""

    def __init__(
        self,
        session_factory: ProductionSessionFactory,
        decision_store: OrchestrationDecisionStore,
        lease_manager: ProductionLeaseManager,
        *,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
    ) -> None:
        self._session_factory = session_factory
        self._decision_store = decision_store
        self._lease_manager = lease_manager
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def recover(self) -> RuntimeRecoveryResult:
        return RuntimeRecoveryResult(
            requeued_job_ids=await self.requeue_due_retries(),
            expired_lease_job_ids=self._lease_manager.expired_job_ids(),
        )

    async def requeue_due_retries(self) -> tuple[UUID, ...]:
        now = self._clock()
        candidates = self._retry_candidates()
        requeued: list[UUID] = []
        for job, retry_event, next_sequence in candidates:
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
                sequence_number=next_sequence,
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
                # Another local worker won the durable requeue race.
                continue
            requeued.append(job.job_id)
        return tuple(requeued)

    def _retry_candidates(
        self,
    ) -> list[tuple[ProductionJob, ProductionRetryScheduled, int]]:
        candidates: list[tuple[ProductionJob, ProductionRetryScheduled, int]] = []
        with self._session_factory() as session:
            job_records = list(
                session.scalars(
                    select(ProductionJobRecord)
                    .where(
                        ProductionJobRecord.status
                        == ProductionJobStatus.WAITING_FOR_RETRY.value
                    )
                    .order_by(ProductionJobRecord.created_at, ProductionJobRecord.job_id)
                )
            )
            for record in job_records:
                event_record = session.scalar(
                    select(ProductionEventRecord)
                    .where(
                        ProductionEventRecord.job_id == record.job_id,
                        ProductionEventRecord.event_type
                        == ProductionEventType.RETRY_SCHEDULED.value,
                    )
                    .order_by(ProductionEventRecord.sequence_number.desc())
                    .limit(1)
                )
                if event_record is None:
                    continue
                event = ProductionEventMapper.to_domain(event_record)
                if not isinstance(event, ProductionRetryScheduled):
                    continue
                max_sequence = session.scalar(
                    select(func.max(ProductionEventRecord.sequence_number)).where(
                        ProductionEventRecord.job_id == record.job_id
                    )
                )
                candidates.append(
                    (ProductionJobMapper.to_domain(record), event, (max_sequence or 0) + 1)
                )
        return candidates
