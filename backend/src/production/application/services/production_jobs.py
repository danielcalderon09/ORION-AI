"""HTTP-independent Production Job commands and queries."""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from backend.src.production.application.events import (
    ProductionCancellationRequested,
    ProductionEventType,
    ProductionJobQueued,
)
from backend.src.production.application.orchestration import (
    OrchestrationDecision,
    PipelineConfiguration,
    ProductionOrchestrator,
    TransitionPolicy,
)
from backend.src.production.application.ports.query_repositories import (
    ProductionArtifactQueryRepository,
    ProductionEventQueryRepository,
    ProductionJobQueryRepository,
)
from backend.src.production.application.sanitization import (
    UnsafeProductionDataError,
    validate_safe_json,
)
from backend.src.production.application.services.exceptions import (
    ProductionJobConflictError,
    ProductionJobNotFoundError,
    ProductionJobStateError,
    ProductionRequestIdConflictError,
    ProductionValidationError,
)
from backend.src.production.application.services.models import (
    CreateProductionJobCommand,
    ProductionArtifactPage,
    ProductionEventPage,
    ProductionJobPage,
    ProductionJobView,
    ProductionOperationResult,
)
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionConcurrencyError,
    ProductionRecordIntegrityError,
)
from backend.src.production.runtime.blocking_executor import RuntimeBlockingExecutor
from backend.src.production.runtime.decision_persister import RuntimeDecisionPersister


def production_request_fingerprint(command: CreateProductionJobCommand) -> str:
    payload = {
        "prompt": " ".join(command.prompt.split()),
        "configuration": command.configuration,
        "generate_clips_after_render": command.generate_clips_after_render,
        "metadata": command.metadata,
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CreateProductionJobService:
    def __init__(
        self,
        *,
        query: ProductionJobQueryRepository,
        blocking_executor: RuntimeBlockingExecutor,
        persister: RuntimeDecisionPersister,
        orchestrator: ProductionOrchestrator,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
    ) -> None:
        self._query = query
        self._blocking = blocking_executor
        self._persister = persister
        self._orchestrator = orchestrator
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def execute(self, command: CreateProductionJobCommand) -> ProductionJobView:
        try:
            configuration = validate_safe_json(command.configuration, path="configuration")
            metadata = validate_safe_json(command.metadata, path="metadata")
        except (UnsafeProductionDataError, TypeError, ValueError) as exc:
            raise ProductionValidationError(str(exc)) from exc
        prompt = " ".join(command.prompt.split())
        if not prompt:
            raise ProductionValidationError("prompt must not be empty")
        fingerprint = production_request_fingerprint(
            command.model_copy(update={"prompt": prompt, "configuration": configuration, "metadata": metadata})
        )
        if command.client_request_id:
            existing = await self._blocking.run(
                self._query.get_by_client_request_id, command.client_request_id
            )
            if existing is not None:
                return self._resolve_existing(existing, fingerprint)

        now = self._clock()
        job = ProductionJob(
            job_id=self._uuid_factory(),
            prompt=prompt,
            created_at=now,
            updated_at=now,
            configuration_snapshot={
                "configuration": configuration,
                "metadata": metadata,
                "generate_clips_after_render": command.generate_clips_after_render,
            },
            client_request_id=command.client_request_id,
            request_fingerprint=fingerprint if command.client_request_id else None,
        )
        decision = self._orchestrator.decide(
            job,
            PipelineConfiguration(
                generate_clips_after_render=command.generate_clips_after_render
            ),
            next_sequence_number=0,
        )
        try:
            await self._persister.persist_decision(previous_job=job, decision=decision)
        except ProductionRecordIntegrityError:
            if not command.client_request_id:
                raise
            existing = await self._blocking.run(
                self._query.get_by_client_request_id, command.client_request_id
            )
            if existing is None:
                raise
            return self._resolve_existing(existing, fingerprint)
        created = await self._blocking.run(self._query.get, job.job_id)
        if created is None:
            raise ProductionJobConflictError("created job could not be reloaded")
        return created

    @staticmethod
    def _resolve_existing(existing: ProductionJobView, fingerprint: str) -> ProductionJobView:
        if existing.job.request_fingerprint != fingerprint:
            raise ProductionRequestIdConflictError(
                "client_request_id was already used with different content"
            )
        return existing


class GetProductionJobService:
    def __init__(self, query: ProductionJobQueryRepository, blocking: RuntimeBlockingExecutor) -> None:
        self._query, self._blocking = query, blocking

    async def execute(self, job_id: UUID) -> ProductionJobView:
        view = await self._blocking.run(self._query.get, job_id)
        if view is None:
            raise ProductionJobNotFoundError(f"production job not found: {job_id}")
        return view


class ListProductionJobsService:
    def __init__(self, query: ProductionJobQueryRepository, blocking: RuntimeBlockingExecutor) -> None:
        self._query, self._blocking = query, blocking

    async def execute(
        self,
        *,
        status: ProductionJobStatus | None,
        stage: ProductionStage | None,
        limit: int,
        offset: int,
    ) -> ProductionJobPage:
        return await self._blocking.run(
            self._query.list, status=status, stage=stage, limit=limit, offset=offset
        )


class CancelProductionJobService:
    def __init__(
        self,
        *,
        query: ProductionJobQueryRepository,
        events: ProductionEventQueryRepository,
        blocking: RuntimeBlockingExecutor,
        persister: RuntimeDecisionPersister,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
    ) -> None:
        self._query, self._events, self._blocking = query, events, blocking
        self._persister, self._clock, self._uuid_factory = persister, clock, uuid_factory

    async def execute(self, job_id: UUID) -> ProductionOperationResult:
        view = await GetProductionJobService(self._query, self._blocking).execute(job_id)
        job = view.job
        if job.status in {ProductionJobStatus.CANCEL_REQUESTED, ProductionJobStatus.CANCELLED}:
            return ProductionOperationResult(job=view, operation="cancel", idempotent=True)
        if job.status in {ProductionJobStatus.COMPLETED, ProductionJobStatus.FAILED}:
            raise ProductionJobStateError(f"cannot cancel job in {job.status.value}")
        TransitionPolicy.validate_transition(job.status, ProductionJobStatus.CANCEL_REQUESTED)
        now = self._clock()
        updated = job.model_copy(update={"status": ProductionJobStatus.CANCEL_REQUESTED, "updated_at": now})
        sequence = await self._blocking.run(self._events.next_sequence, job_id)
        event = ProductionCancellationRequested(
            event_id=self._uuid_factory(), job_id=job_id, occurred_at=now,
            sequence_number=sequence, correlation_id=job_id, reason="api_request",
        )
        try:
            await self._persister.persist_decision(
                previous_job=job,
                decision=OrchestrationDecision(
                    updated_job=updated, events=(event,), should_continue=False,
                    reason="cancellation_requested",
                ),
            )
        except ProductionConcurrencyError as exc:
            current = await GetProductionJobService(self._query, self._blocking).execute(job_id)
            if current.job.status in {ProductionJobStatus.CANCEL_REQUESTED, ProductionJobStatus.CANCELLED}:
                return ProductionOperationResult(job=current, operation="cancel", idempotent=True)
            raise ProductionJobConflictError("job changed during cancellation") from exc
        current = await GetProductionJobService(self._query, self._blocking).execute(job_id)
        return ProductionOperationResult(job=current, operation="cancel")


class RetryProductionJobService:
    def __init__(
        self,
        *,
        query: ProductionJobQueryRepository,
        events: ProductionEventQueryRepository,
        blocking: RuntimeBlockingExecutor,
        persister: RuntimeDecisionPersister,
        clock: Callable[[], datetime],
        uuid_factory: Callable[[], UUID],
    ) -> None:
        self._query, self._events, self._blocking = query, events, blocking
        self._persister, self._clock, self._uuid_factory = persister, clock, uuid_factory

    async def execute(self, job_id: UUID) -> ProductionOperationResult:
        view = await GetProductionJobService(self._query, self._blocking).execute(job_id)
        job = view.job
        if job.status is ProductionJobStatus.QUEUED:
            latest = await self._blocking.run(self._events.latest_for_job, job_id)
            if latest and latest.event_type is ProductionEventType.JOB_QUEUED and latest.metadata.get("operation") == "manual_retry":
                return ProductionOperationResult(job=view, operation="retry", idempotent=True)
        if job.status not in {ProductionJobStatus.FAILED, ProductionJobStatus.NEEDS_USER_ACTION}:
            raise ProductionJobStateError(f"cannot retry job in {job.status.value}")
        TransitionPolicy.validate_transition(
            job.status, ProductionJobStatus.QUEUED,
            allow_failed_recovery=job.status is ProductionJobStatus.FAILED,
        )
        now = self._clock()
        updated = job.model_copy(update={
            "status": ProductionJobStatus.QUEUED, "updated_at": now,
            "error_code": None, "error_message": None,
        })
        sequence = await self._blocking.run(self._events.next_sequence, job_id)
        event = ProductionJobQueued(
            event_id=self._uuid_factory(), job_id=job_id, occurred_at=now,
            sequence_number=sequence, correlation_id=job_id,
            metadata={"operation": "manual_retry"},
        )
        try:
            await self._persister.persist_decision(
                previous_job=job,
                decision=OrchestrationDecision(
                    updated_job=updated, events=(event,), should_continue=True,
                    reason="manual_retry",
                ),
            )
        except ProductionConcurrencyError as exc:
            current = await GetProductionJobService(self._query, self._blocking).execute(job_id)
            if current.job.status is ProductionJobStatus.QUEUED:
                return ProductionOperationResult(job=current, operation="retry", idempotent=True)
            raise ProductionJobConflictError("job changed during retry") from exc
        current = await GetProductionJobService(self._query, self._blocking).execute(job_id)
        return ProductionOperationResult(job=current, operation="retry")


class ListProductionEventsService:
    def __init__(
        self,
        jobs: ProductionJobQueryRepository,
        events: ProductionEventQueryRepository,
        blocking: RuntimeBlockingExecutor,
    ) -> None:
        self._jobs, self._events, self._blocking = jobs, events, blocking

    async def execute(self, job_id: UUID) -> ProductionEventPage:
        await GetProductionJobService(self._jobs, self._blocking).execute(job_id)
        return ProductionEventPage(items=await self._blocking.run(self._events.list_for_job, job_id))


class ListProductionArtifactsService:
    def __init__(
        self,
        jobs: ProductionJobQueryRepository,
        artifacts: ProductionArtifactQueryRepository,
        blocking: RuntimeBlockingExecutor,
    ) -> None:
        self._jobs, self._artifacts, self._blocking = jobs, artifacts, blocking

    async def execute(self, job_id: UUID) -> ProductionArtifactPage:
        await GetProductionJobService(self._jobs, self._blocking).execute(job_id)
        return await self._blocking.run(self._artifacts.list_for_job, job_id)
