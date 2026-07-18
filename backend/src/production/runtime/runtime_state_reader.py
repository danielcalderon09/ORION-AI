"""Read-only boundary over durable production runtime state."""

from uuid import UUID

from sqlalchemy import func, select

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.events import (
    ProductionEventType,
    ProductionRetryScheduled,
)
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.mappers.command_mapper import (
    StageCommandMapper,
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
    StageCommandRecord,
)
from backend.src.production.infrastructure.persistence.session import ProductionSessionFactory
from backend.src.production.runtime.runtime_models import RuntimeRetryCandidate


class RuntimeStateIntegrityError(RuntimeError):
    """Raised when durable runtime state cannot be interpreted safely."""


class MultiplePendingStageCommandsError(RuntimeStateIntegrityError):
    """Raised instead of silently selecting one of several pending commands."""


class RuntimeStateReader:
    """Own short-lived read sessions and return only validated contracts."""

    def __init__(self, session_factory: ProductionSessionFactory) -> None:
        self._session_factory = session_factory

    def load_job(self, job_id: UUID) -> ProductionJob:
        with self._session_factory() as session:
            record = session.get(ProductionJobRecord, str(job_id))
            if record is None:
                raise RuntimeStateIntegrityError(f"production job does not exist: {job_id}")
            return ProductionJobMapper.to_domain(record)

    def find_unprocessed_command(self, job_id: UUID) -> StageCommand | None:
        with self._session_factory() as session:
            records = list(
                session.scalars(
                    select(StageCommandRecord)
                    .where(
                        StageCommandRecord.job_id == str(job_id),
                        StageCommandRecord.processed_at.is_(None),
                    )
                    .order_by(StageCommandRecord.created_at, StageCommandRecord.command_id)
                    .limit(2)
                )
            )
            if len(records) > 1:
                raise MultiplePendingStageCommandsError(
                    f"job {job_id} has multiple unprocessed stage commands"
                )
            return StageCommandMapper.to_domain(records[0]) if records else None

    def next_event_sequence(self, job_id: UUID) -> int:
        with self._session_factory() as session:
            current = session.scalar(
                select(func.max(ProductionEventRecord.sequence_number)).where(
                    ProductionEventRecord.job_id == str(job_id)
                )
            )
            return 0 if current is None else current + 1

    def next_attempt_number(self, job_id: UUID, stage: ProductionStage) -> int:
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

    def latest_retry_event(self, job_id: UUID) -> ProductionRetryScheduled | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(ProductionEventRecord)
                .where(
                    ProductionEventRecord.job_id == str(job_id),
                    ProductionEventRecord.event_type
                    == ProductionEventType.RETRY_SCHEDULED.value,
                )
                .order_by(ProductionEventRecord.sequence_number.desc())
                .limit(1)
            )
            if record is None:
                return None
            event = ProductionEventMapper.to_domain(record)
            if not isinstance(event, ProductionRetryScheduled):
                raise RuntimeStateIntegrityError("retry event record mapped to an invalid type")
            return event

    def list_retry_candidates(self) -> tuple[RuntimeRetryCandidate, ...]:
        with self._session_factory() as session:
            job_ids = tuple(
                UUID(value)
                for value in session.scalars(
                    select(ProductionJobRecord.job_id)
                    .where(
                        ProductionJobRecord.status
                        == ProductionJobStatus.WAITING_FOR_RETRY.value
                    )
                    .order_by(ProductionJobRecord.created_at, ProductionJobRecord.job_id)
                )
            )
        candidates: list[RuntimeRetryCandidate] = []
        for job_id in job_ids:
            job = self.load_job(job_id)
            if job.status is not ProductionJobStatus.WAITING_FOR_RETRY:
                # Expected read race: another process already requeued this job.
                continue
            event = self.latest_retry_event(job_id)
            if event is None:
                raise RuntimeStateIntegrityError(
                    f"waiting-for-retry job has no retry event: {job_id}"
                )
            candidates.append(
                RuntimeRetryCandidate(
                    job=job,
                    retry_event=event,
                    next_sequence_number=self.next_event_sequence(job_id),
                )
            )
        return tuple(candidates)
