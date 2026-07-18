"""Local lease-aware worker for one-decision production processing."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.orchestration import (
    PipelineConfiguration,
    ProductionOrchestrator,
)
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.mappers.command_mapper import (
    StageCommandMapper,
)
from backend.src.production.infrastructure.persistence.mappers.production_job_mapper import (
    ProductionJobMapper,
)
from backend.src.production.infrastructure.persistence.models import (
    ProductionEventRecord,
    ProductionJobRecord,
    StageCommandRecord,
)
from backend.src.production.infrastructure.persistence.session import ProductionSessionFactory
from backend.src.production.infrastructure.persistence.transactions import (
    OrchestrationDecisionStore,
)
from backend.src.production.runtime.executor import ProductionExecutor
from backend.src.production.runtime.heartbeat import ProductionHeartbeat
from backend.src.production.runtime.lease_manager import ProductionLeaseManager
from backend.src.production.runtime.recovery import ProductionRecoveryService
from backend.src.production.runtime.runtime_models import WorkerRunResult


class ProductionRuntimeError(RuntimeError):
    """Raised when durable runtime state cannot be executed safely."""


class ProductionWorker:
    """Claim one job and persist one orchestration decision per cycle."""

    CLAIMABLE_STATUSES = frozenset(
        {
            ProductionJobStatus.QUEUED,
            ProductionJobStatus.RUNNING,
            ProductionJobStatus.CANCEL_REQUESTED,
        }
    )

    def __init__(
        self,
        session_factory: ProductionSessionFactory,
        *,
        owner_id: str,
        orchestrator: ProductionOrchestrator,
        configuration: PipelineConfiguration,
        decision_store: OrchestrationDecisionStore,
        lease_manager: ProductionLeaseManager,
        heartbeat: ProductionHeartbeat,
        executor: ProductionExecutor,
        recovery: ProductionRecoveryService,
        clock: Callable[[], datetime],
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        self._session_factory = session_factory
        self._owner_id = owner_id
        self._orchestrator = orchestrator
        self._configuration = configuration
        self._decision_store = decision_store
        self._lease_manager = lease_manager
        self._heartbeat = heartbeat
        self._executor = executor
        self._recovery = recovery
        self._clock = clock

    async def run_once(self) -> WorkerRunResult:
        await self._recovery.requeue_due_retries()
        lease = self._lease_manager.acquire_next(
            owner_id=self._owner_id,
            statuses=self.CLAIMABLE_STATUSES,
        )
        if lease is None:
            return WorkerRunResult(processed=False, reason="no_claimable_job")

        try:
            job = self._load_job(lease.job_id)
            if job.status is ProductionJobStatus.QUEUED:
                decision = self._orchestrator.decide(
                    job,
                    self._configuration,
                    next_sequence_number=self._next_sequence(job.job_id),
                    next_attempt_number=self._next_attempt(job.job_id, job.current_stage),
                )
                await self._decision_store.persist_decision(
                    previous_job=job,
                    decision=decision,
                )
                return self._result(job, decision.updated_job, decision.next_command)

            if job.status is ProductionJobStatus.CANCEL_REQUESTED:
                command = self._unprocessed_command(job.job_id)
                if command is not None:
                    async with self._heartbeat.maintain(
                        job_id=job.job_id,
                        owner_id=self._owner_id,
                    ):
                        execution = await self._executor.execute(command)
                    current_job = self._load_job(job.job_id)
                    decision = self._orchestrator.decide(
                        current_job,
                        self._configuration,
                        next_sequence_number=self._next_sequence(job.job_id),
                    )
                    await self._decision_store.persist_decision(
                        previous_job=current_job,
                        decision=decision,
                        processed_command=command,
                        processed_result=execution.result,
                        artifacts=execution.artifacts,
                    )
                    return self._result(current_job, decision.updated_job, None)
                decision = self._orchestrator.decide(
                    job,
                    self._configuration,
                    next_sequence_number=self._next_sequence(job.job_id),
                )
                await self._decision_store.persist_decision(
                    previous_job=job,
                    decision=decision,
                )
                return self._result(job, decision.updated_job, None)

            if job.status is not ProductionJobStatus.RUNNING:
                raise ProductionRuntimeError(f"claimed unsupported status {job.status.value}")
            command = self._unprocessed_command(job.job_id)
            if command is None:
                raise ProductionRuntimeError(
                    f"running job {job.job_id} has no unprocessed command"
                )
            async with self._heartbeat.maintain(
                job_id=job.job_id,
                owner_id=self._owner_id,
            ):
                execution = await self._executor.execute(command)

            current_job = self._load_job(job.job_id)
            decision = self._orchestrator.decide(
                current_job,
                self._configuration,
                last_command=command,
                last_result=execution.result,
                next_sequence_number=self._next_sequence(job.job_id),
            )
            await self._decision_store.persist_decision(
                previous_job=current_job,
                decision=decision,
                processed_command=command,
                processed_result=execution.result,
                artifacts=execution.artifacts,
            )
            return self._result(current_job, decision.updated_job, decision.next_command)
        finally:
            self._lease_manager.release(job_id=lease.job_id, owner_id=self._owner_id)

    async def run_until_idle(self, *, max_cycles: int = 100) -> tuple[WorkerRunResult, ...]:
        if max_cycles < 1:
            raise ValueError("max_cycles must be positive")
        results: list[WorkerRunResult] = []
        for _ in range(max_cycles):
            result = await self.run_once()
            results.append(result)
            if not result.processed:
                return tuple(results)
        raise ProductionRuntimeError("worker did not become idle within max_cycles")

    async def run_forever(
        self,
        *,
        stop_event: asyncio.Event,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        while not stop_event.is_set():
            result = await self.run_once()
            if not result.processed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=poll_interval_seconds,
                    )

    def _load_job(self, job_id: UUID) -> ProductionJob:
        with self._session_factory() as session:
            record = session.get(ProductionJobRecord, str(job_id))
            if record is None:
                raise ProductionRuntimeError(f"leased job disappeared: {job_id}")
            return ProductionJobMapper.to_domain(record)

    def _unprocessed_command(self, job_id: UUID) -> StageCommand | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(StageCommandRecord)
                .where(
                    StageCommandRecord.job_id == str(job_id),
                    StageCommandRecord.processed_at.is_(None),
                )
                .order_by(StageCommandRecord.created_at.desc(), StageCommandRecord.command_id)
                .limit(1)
            )
            return StageCommandMapper.to_domain(record) if record else None

    def _next_sequence(self, job_id: UUID) -> int:
        with self._session_factory() as session:
            current = session.scalar(
                select(func.max(ProductionEventRecord.sequence_number)).where(
                    ProductionEventRecord.job_id == str(job_id)
                )
            )
            return 0 if current is None else current + 1

    def _next_attempt(self, job_id: UUID, stage: ProductionStage) -> int:
        if stage is ProductionStage.CREATED:
            return 1
        with self._session_factory() as session:
            current = session.scalar(
                select(func.max(StageCommandRecord.attempt_number)).where(
                    StageCommandRecord.job_id == str(job_id),
                    StageCommandRecord.stage == stage.value,
                )
            )
            return 1 if current is None else current + 1

    @staticmethod
    def _result(
        previous: ProductionJob,
        updated: ProductionJob,
        command: StageCommand | None,
    ) -> WorkerRunResult:
        return WorkerRunResult(
            processed=True,
            job_id=updated.job_id,
            previous_status=previous.status,
            updated_status=updated.status,
            updated_stage=updated.current_stage,
            command_id=command.command_id if command else None,
            reason="decision_persisted",
        )
