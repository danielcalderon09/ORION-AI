"""Atomic and idempotent storage of one orchestration decision."""

from collections.abc import Callable, Collection
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.src.production.application.commands.stage_command import StageCommand
from backend.src.production.application.events.production_events import (
    ProductionEventUnion,
    ProductionStageStarted,
)
from backend.src.production.application.orchestration.production_orchestrator import (
    OrchestrationDecision,
    validate_stage_result,
)
from backend.src.production.application.results.stage_result import StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionConcurrencyError,
    ProductionEventSequenceError,
    ProductionIdempotencyConflictError,
    ProductionRecordIntegrityError,
)
from backend.src.production.infrastructure.persistence.mappers.command_mapper import (
    StageCommandMapper,
)
from backend.src.production.infrastructure.persistence.mappers.event_mapper import (
    ProductionEventMapper,
)
from backend.src.production.infrastructure.persistence.mappers.result_mapper import (
    StageResultMapper,
)
from backend.src.production.infrastructure.persistence.models import (
    ArtifactRecord,
    ProductionEventRecord,
    ProductionStageRunRecord,
    ProductionStageRunStatus,
    StageCommandRecord,
    StageResultRecord,
)
from backend.src.production.infrastructure.persistence.session import ProductionSessionFactory
from backend.src.production.infrastructure.persistence.transactions.unit_of_work import (
    ProductionUnitOfWork,
)


class PersistedDecision(ContractModel):
    """Receipt returned only after the complete transaction commits."""

    job: ProductionJob
    row_version: int = Field(ge=1)
    persisted_command_ids: tuple[UUID, ...] = ()
    persisted_event_ids: tuple[UUID, ...] = ()
    idempotent_replay: bool = False


class OrchestrationDecisionStore:
    """Persist job, commands, result, runs, artifacts, and events atomically."""

    def __init__(
        self,
        session_factory: ProductionSessionFactory,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def persist_decision(
        self,
        *,
        previous_job: ProductionJob,
        decision: OrchestrationDecision,
        processed_command: StageCommand | None = None,
        processed_result: StageResult | None = None,
        artifacts: Collection[Artifact] = (),
    ) -> PersistedDecision:
        if decision.updated_job.job_id != previous_job.job_id:
            raise ProductionRecordIntegrityError("decision cannot change job_id")
        if processed_result is not None and processed_command is None:
            raise ProductionRecordIntegrityError("processed_result requires processed_command")
        if processed_command is not None and processed_command.job_id != previous_job.job_id:
            raise ProductionRecordIntegrityError("processed command belongs to another job")
        if processed_result is not None and processed_command is not None:
            validate_stage_result(processed_command, processed_result)

        async with ProductionUnitOfWork(
            self._session_factory,
            clock=self._clock,
        ) as uow:
            try:
                current = await uow.jobs.get(previous_job.job_id)
                idempotent_replay = current == decision.updated_job
                if current is None:
                    await uow.jobs.add(decision.updated_job)
                elif not idempotent_replay:
                    if current != previous_job:
                        raise ProductionConcurrencyError(
                            f"production job changed concurrently: {previous_job.job_id}"
                        )
                    await uow.jobs.save(decision.updated_job)

                for artifact in artifacts:
                    if artifact.job_id != previous_job.job_id:
                        raise ProductionRecordIntegrityError(
                            f"artifact {artifact.artifact_id} belongs to another job"
                        )
                    await uow.artifacts.save(artifact)

                persisted_commands: list[UUID] = []
                if processed_command is not None:
                    self._persist_command(uow.session, processed_command)
                    persisted_commands.append(processed_command.command_id)
                if decision.next_command is not None:
                    self._persist_command(uow.session, decision.next_command)
                    persisted_commands.append(decision.next_command.command_id)
                uow.session.flush()

                if processed_result is not None and processed_command is not None:
                    self._persist_result(uow.session, processed_result, db_now=self._clock())
                    command_record = uow.session.get(
                        StageCommandRecord,
                        str(processed_command.command_id),
                    )
                    if command_record is None:
                        raise ProductionRecordIntegrityError("processed command was not persisted")
                    if command_record.processed_at not in {None, processed_result.finished_at}:
                        raise ProductionIdempotencyConflictError(
                            f"command {processed_command.command_id} has another processed_at"
                        )
                    command_record.processed_at = processed_result.finished_at
                uow.session.flush()

                self._validate_artifact_references(
                    uow.session,
                    processed_result=processed_result,
                    next_command=decision.next_command,
                )
                self._persist_stage_runs(
                    uow.session,
                    decision=decision,
                    processed_command=processed_command,
                    processed_result=processed_result,
                )
                persisted_events = self._persist_events(
                    uow.session,
                    job_id=previous_job.job_id,
                    events=decision.events,
                    db_now=self._clock(),
                )
                uow.session.flush()

                row_version = uow.jobs.row_version(previous_job.job_id)
                if row_version is None:
                    raise ProductionRecordIntegrityError("updated job has no row_version")
                await uow.commit()
            except IntegrityError as exc:
                raise ProductionRecordIntegrityError(
                    "orchestration decision violated a database constraint"
                ) from exc

        return PersistedDecision(
            job=decision.updated_job,
            row_version=row_version,
            persisted_command_ids=tuple(dict.fromkeys(persisted_commands)),
            persisted_event_ids=tuple(persisted_events),
            idempotent_replay=idempotent_replay,
        )

    @staticmethod
    def _persist_command(session: Session, command: StageCommand) -> None:
        existing = session.get(StageCommandRecord, str(command.command_id))
        if existing is not None:
            if StageCommandMapper.to_domain(existing) != command:
                raise ProductionIdempotencyConflictError(
                    f"command {command.command_id} has different content"
                )
            return
        key_owner = session.scalar(
            select(StageCommandRecord).where(
                StageCommandRecord.idempotency_key == command.idempotency_key
            )
        )
        if key_owner is not None:
            raise ProductionIdempotencyConflictError(
                f"idempotency key belongs to command {key_owner.command_id}"
            )
        session.add(StageCommandMapper.to_record(command))

    @staticmethod
    def _persist_result(session: Session, result: StageResult, *, db_now: datetime) -> None:
        existing = session.get(StageResultRecord, str(result.command_id))
        if existing is not None:
            if StageResultMapper.to_domain(existing) != result:
                raise ProductionIdempotencyConflictError(
                    f"result for command {result.command_id} has different content"
                )
            return
        session.add(StageResultMapper.to_record(result, db_now=db_now))

    @staticmethod
    def _validate_artifact_references(
        session: Session,
        *,
        processed_result: StageResult | None,
        next_command: StageCommand | None,
    ) -> None:
        referenced_ids: set[UUID] = set()
        if processed_result is not None:
            referenced_ids.update(processed_result.output_artifact_ids)
        if next_command is not None:
            referenced_ids.update(next_command.input_artifact_ids)
        for artifact_id in referenced_ids:
            if session.get(ArtifactRecord, str(artifact_id)) is None:
                raise ProductionRecordIntegrityError(
                    f"referenced artifact is not registered: {artifact_id}"
                )

    def _persist_stage_runs(
        self,
        session: Session,
        *,
        decision: OrchestrationDecision,
        processed_command: StageCommand | None,
        processed_result: StageResult | None,
    ) -> None:
        if processed_command is not None and processed_result is not None:
            self._upsert_stage_run(
                session,
                processed_command,
                status=ProductionStageRunStatus(processed_result.outcome.value),
                started_at=processed_result.started_at,
                finished_at=processed_result.finished_at,
                result_id=processed_result.command_id,
            )
        if decision.next_command is not None:
            started_event = next(
                (
                    event
                    for event in decision.events
                    if isinstance(event, ProductionStageStarted)
                    and event.command_id == decision.next_command.command_id
                ),
                None,
            )
            self._upsert_stage_run(
                session,
                decision.next_command,
                status=(
                    ProductionStageRunStatus.RUNNING
                    if started_event
                    else ProductionStageRunStatus.PENDING
                ),
                started_at=started_event.occurred_at if started_event else None,
                finished_at=None,
                result_id=None,
            )

    def _upsert_stage_run(
        self,
        session: Session,
        command: StageCommand,
        *,
        status: ProductionStageRunStatus,
        started_at: datetime | None,
        finished_at: datetime | None,
        result_id: UUID | None,
    ) -> None:
        stage_run_id = str(uuid5(NAMESPACE_URL, f"orion:stage-run:{command.command_id}"))
        existing = session.get(ProductionStageRunRecord, stage_run_id)
        if existing is None:
            attempt_owner = session.scalar(
                select(ProductionStageRunRecord).where(
                    ProductionStageRunRecord.job_id == str(command.job_id),
                    ProductionStageRunRecord.stage == command.stage.value,
                    ProductionStageRunRecord.attempt_number == command.attempt_number,
                )
            )
            if attempt_owner is not None:
                raise ProductionIdempotencyConflictError(
                    "stage attempt already belongs to another command"
                )
            now = self._clock()
            session.add(
                ProductionStageRunRecord(
                    stage_run_id=stage_run_id,
                    job_id=str(command.job_id),
                    stage=command.stage.value,
                    attempt_number=command.attempt_number,
                    status=status.value,
                    command_id=str(command.command_id),
                    result_id=str(result_id) if result_id else None,
                    idempotency_key=command.idempotency_key,
                    started_at=started_at,
                    finished_at=finished_at,
                    created_at=now,
                    updated_at=now,
                )
            )
            return

        immutable_values = (
            existing.job_id,
            existing.stage,
            existing.attempt_number,
            existing.command_id,
            existing.idempotency_key,
        )
        expected_values = (
            str(command.job_id),
            command.stage.value,
            command.attempt_number,
            str(command.command_id),
            command.idempotency_key,
        )
        if immutable_values != expected_values:
            raise ProductionIdempotencyConflictError(
                f"stage run {stage_run_id} has different command data"
            )
        if existing.result_id not in {None, str(result_id) if result_id else None}:
            raise ProductionIdempotencyConflictError(
                f"stage run {stage_run_id} has another result"
            )
        existing.status = status.value
        existing.result_id = str(result_id) if result_id else existing.result_id
        existing.started_at = started_at or existing.started_at
        existing.finished_at = finished_at or existing.finished_at
        existing.updated_at = self._clock()

    @staticmethod
    def _persist_events(
        session: Session,
        *,
        job_id: UUID,
        events: tuple[ProductionEventUnion, ...],
        db_now: datetime,
    ) -> list[UUID]:
        existing_records = list(
            session.scalars(
                select(ProductionEventRecord)
                .where(ProductionEventRecord.job_id == str(job_id))
                .order_by(ProductionEventRecord.sequence_number)
            )
        )
        by_id = {record.event_id: record for record in existing_records}
        by_sequence = {record.sequence_number: record for record in existing_records}
        expected_sequence = (
            max(by_sequence) + 1 if by_sequence else 0
        )
        persisted: list[UUID] = []

        for event in events:
            if event.job_id != job_id:
                raise ProductionRecordIntegrityError(
                    f"event {event.event_id} belongs to another job"
                )
            existing = by_id.get(str(event.event_id))
            if existing is not None:
                if ProductionEventMapper.to_domain(existing) != event:
                    raise ProductionIdempotencyConflictError(
                        f"event {event.event_id} has different content"
                    )
                persisted.append(event.event_id)
                continue
            sequence_owner = by_sequence.get(event.sequence_number)
            if sequence_owner is not None:
                raise ProductionEventSequenceError(
                    f"sequence {event.sequence_number} belongs to {sequence_owner.event_id}"
                )
            if event.sequence_number != expected_sequence:
                raise ProductionEventSequenceError(
                    f"expected event sequence {expected_sequence}, got {event.sequence_number}"
                )
            record = ProductionEventMapper.to_record(event, db_now=db_now)
            session.add(record)
            by_id[record.event_id] = record
            by_sequence[record.sequence_number] = record
            expected_sequence += 1
            persisted.append(event.event_id)
        return persisted
