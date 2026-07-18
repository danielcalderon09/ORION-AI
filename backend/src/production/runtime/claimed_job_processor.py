"""Process exactly one durable decision for an already claimed job."""

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.orchestration import (
    PipelineConfiguration,
    ProductionOrchestrator,
)
from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.runtime.blocking_executor import RuntimeBlockingExecutor
from backend.src.production.runtime.context import StageContextFactory
from backend.src.production.runtime.decision_persister import RuntimeDecisionPersister
from backend.src.production.runtime.executor import ProductionExecutor
from backend.src.production.runtime.heartbeat import ProductionHeartbeat
from backend.src.production.runtime.runtime_models import (
    ProductionLease,
    StageExecutionOutput,
    WorkerRunResult,
)
from backend.src.production.runtime.runtime_state_reader import RuntimeStateReader


class ProductionRuntimeError(RuntimeError):
    """Raised when a claimed job cannot be processed safely."""


class ClaimedJobProcessor:
    """Coordinate state, orchestration, execution, and atomic persistence after claim."""

    def __init__(
        self,
        *,
        state_reader: RuntimeStateReader,
        blocking_executor: RuntimeBlockingExecutor,
        orchestrator: ProductionOrchestrator,
        configuration: PipelineConfiguration,
        decision_store: RuntimeDecisionPersister,
        heartbeat: ProductionHeartbeat,
        executor: ProductionExecutor,
        context_factory: StageContextFactory,
    ) -> None:
        self._state_reader = state_reader
        self._blocking_executor = blocking_executor
        self._orchestrator = orchestrator
        self._configuration = configuration
        self._decision_store = decision_store
        self._heartbeat = heartbeat
        self._executor = executor
        self._context_factory = context_factory

    async def process(
        self,
        *,
        lease: ProductionLease,
        owner_id: str,
    ) -> WorkerRunResult:
        job = await self._blocking_executor.run(self._state_reader.load_job, lease.job_id)
        if job.status is ProductionJobStatus.QUEUED:
            return await self._process_queued(job)
        if job.status is ProductionJobStatus.CANCEL_REQUESTED:
            return await self._process_cancel_requested(job, owner_id=owner_id)
        if job.status is ProductionJobStatus.RUNNING:
            return await self._process_running(job, owner_id=owner_id)
        raise ProductionRuntimeError(f"claimed unsupported status {job.status.value}")

    async def _process_queued(self, job: ProductionJob) -> WorkerRunResult:
        next_sequence = await self._blocking_executor.run(
            self._state_reader.next_event_sequence,
            job.job_id,
        )
        next_attempt = await self._blocking_executor.run(
            self._state_reader.next_attempt_number,
            job.job_id,
            job.current_stage,
        )
        decision = self._orchestrator.decide(
            job,
            self._configuration_for(job),
            next_sequence_number=next_sequence,
            next_attempt_number=next_attempt,
        )
        await self._decision_store.persist_decision(previous_job=job, decision=decision)
        return self._result(job, decision.updated_job, decision.next_command)

    async def _process_cancel_requested(
        self,
        job: ProductionJob,
        *,
        owner_id: str,
    ) -> WorkerRunResult:
        command = await self._pending_command(job)
        if command is None:
            next_sequence = await self._next_sequence(job)
            decision = self._orchestrator.decide(
                job,
                self._configuration_for(job),
                next_sequence_number=next_sequence,
            )
            await self._decision_store.persist_decision(previous_job=job, decision=decision)
            return self._result(job, decision.updated_job, None)

        execution = await self._execute(job, command, owner_id=owner_id)
        current_job = await self._reload(job)
        decision = self._orchestrator.decide(
            current_job,
            self._configuration_for(current_job),
            next_sequence_number=await self._next_sequence(current_job),
        )
        await self._decision_store.persist_decision(
            previous_job=current_job,
            decision=decision,
            processed_command=command,
            processed_result=execution.result,
            artifacts=execution.artifacts,
        )
        return self._result(current_job, decision.updated_job, None)

    async def _process_running(
        self,
        job: ProductionJob,
        *,
        owner_id: str,
    ) -> WorkerRunResult:
        command = await self._pending_command(job)
        if command is None:
            raise ProductionRuntimeError(
                f"running job {job.job_id} has no unprocessed command"
            )
        execution = await self._execute(job, command, owner_id=owner_id)
        current_job = await self._reload(job)
        decision = self._orchestrator.decide(
            current_job,
            self._configuration_for(current_job),
            last_command=command,
            last_result=execution.result,
            next_sequence_number=await self._next_sequence(current_job),
        )
        await self._decision_store.persist_decision(
            previous_job=current_job,
            decision=decision,
            processed_command=command,
            processed_result=execution.result,
            artifacts=execution.artifacts,
        )
        return self._result(current_job, decision.updated_job, decision.next_command)

    async def _execute(
        self,
        job: ProductionJob,
        command: StageCommand,
        *,
        owner_id: str,
    ) -> StageExecutionOutput:
        context = self._context_factory.create(job=job, command=command)
        async with self._heartbeat.maintain(job_id=job.job_id, owner_id=owner_id):
            return await self._executor.execute(command, context)

    async def _pending_command(self, job: ProductionJob) -> StageCommand | None:
        return await self._blocking_executor.run(
            self._state_reader.find_unprocessed_command,
            job.job_id,
        )

    async def _reload(self, job: ProductionJob) -> ProductionJob:
        return await self._blocking_executor.run(self._state_reader.load_job, job.job_id)

    async def _next_sequence(self, job: ProductionJob) -> int:
        return await self._blocking_executor.run(
            self._state_reader.next_event_sequence,
            job.job_id,
        )

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

    def _configuration_for(self, job: ProductionJob) -> PipelineConfiguration:
        """Overlay the durable per-job clip choice on runtime policy defaults."""

        generate_clips = job.configuration_snapshot.get("generate_clips_after_render")
        if not isinstance(generate_clips, bool):
            return self._configuration
        return self._configuration.model_copy(
            update={"generate_clips_after_render": generate_clips}
        )
