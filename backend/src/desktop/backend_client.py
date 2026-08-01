"""Thin desktop adapter over existing Production application services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.services.production_jobs import (
    ListProductionJobsService,
)
from backend.src.production.cli.generate_video import local_mvp_settings
from backend.src.production.composition import build_production_container
from backend.src.production.composition.schema import ensure_production_schema
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.infrastructure.persistence.query_repositories import (
    SQLAlchemyProductionJobQueryRepository,
)
from backend.src.production.infrastructure.persistence.session import (
    create_production_engine,
    create_production_session_factory,
)
from backend.src.production.local_mvp import (
    LocalMvpApplication,
    LocalMvpProgress,
    LocalMvpReport,
    LocalMvpRequest,
)
from backend.src.production.runtime import ImmediateRuntimeBlockingExecutor

ProgressCallback = Callable[[LocalMvpProgress], None]


@dataclass(frozen=True, slots=True)
class DesktopJobSummary:
    job_id: UUID
    title: str
    prompt: str
    status: ProductionJobStatus
    current_stage: ProductionStage
    created_at: datetime
    duration_seconds: int | None

    @property
    def can_resume(self) -> bool:
        return self.status in {
            ProductionJobStatus.QUEUED,
            ProductionJobStatus.RUNNING,
            ProductionJobStatus.FAILED,
            ProductionJobStatus.NEEDS_USER_ACTION,
        }

    @property
    def has_result(self) -> bool:
        return self.status is ProductionJobStatus.COMPLETED


class DesktopBackend(Protocol):
    async def list_jobs(self, *, limit: int = 25) -> tuple[DesktopJobSummary, ...]: ...

    async def run_pipeline(
        self,
        request: LocalMvpRequest,
        *,
        progress_callback: ProgressCallback,
        retry_failed: bool = False,
    ) -> LocalMvpReport: ...


class ProductionDesktopBackend:
    """Compose and invoke Production; never owns production business logic."""

    def __init__(
        self,
        settings_factory: Callable[[], Settings] = local_mvp_settings,
    ) -> None:
        self._settings_factory = settings_factory

    async def list_jobs(self, *, limit: int = 25) -> tuple[DesktopJobSummary, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("desktop job limit must be between 1 and 100")
        settings = self._settings_factory()
        engine = create_production_engine(
            settings.production_database_url,
            echo=settings.ORION_DATABASE_ECHO,
        )
        try:
            await ensure_production_schema(settings, engine)
            sessions = create_production_session_factory(engine)
            service = ListProductionJobsService(
                SQLAlchemyProductionJobQueryRepository(sessions),
                ImmediateRuntimeBlockingExecutor(),
            )
            page = await service.execute(status=None, stage=None, limit=limit, offset=0)
            return tuple(_job_summary(item.job) for item in page.items)
        finally:
            engine.dispose()

    async def run_pipeline(
        self,
        request: LocalMvpRequest,
        *,
        progress_callback: ProgressCallback,
        retry_failed: bool = False,
    ) -> LocalMvpReport:
        settings = self._settings_factory()
        container = build_production_container(settings)
        try:
            await ensure_production_schema(settings, container.engine)
            if request.resume_job_id is not None and retry_failed:
                current = await container.get_job.execute(request.resume_job_id)
                if current.job.status in {
                    ProductionJobStatus.FAILED,
                    ProductionJobStatus.NEEDS_USER_ACTION,
                }:
                    await container.retry_job.execute(request.resume_job_id)
            application = LocalMvpApplication(
                create_job=container.create_job,
                get_job=container.get_job,
                list_artifacts=container.list_artifacts,
                list_events=container.list_events,
                worker=container.worker,
                workspace_root=settings.PROJECTS_DIR,
                configured_renderer=settings.ORION_RENDERER,
                max_validation_manifest_bytes=(
                    settings.ORION_FINAL_RENDER_VALIDATION_MAX_MANIFEST_BYTES
                ),
            )
            return await application.run(request, progress_callback=progress_callback)
        finally:
            await container.aclose()


def _job_summary(job: object) -> DesktopJobSummary:
    from backend.src.production.domain.production_job import ProductionJob

    if not isinstance(job, ProductionJob):
        raise TypeError("desktop job summary requires a ProductionJob")
    snapshot = job.configuration_snapshot
    raw_configuration = snapshot.get("configuration", {})
    duration: int | None = None
    if isinstance(raw_configuration, dict):
        planning = raw_configuration.get("planning", {})
        if isinstance(planning, dict):
            value = planning.get("target_duration_seconds")
            if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
                duration = round(value)
    raw_metadata = snapshot.get("metadata", {})
    title: str | None = None
    if isinstance(raw_metadata, dict):
        value = raw_metadata.get("title")
        if isinstance(value, str) and value.strip():
            title = value.strip()
    return DesktopJobSummary(
        job_id=job.job_id,
        title=title or _short_prompt(job.prompt),
        prompt=job.prompt,
        status=job.status,
        current_stage=job.current_stage,
        created_at=job.created_at,
        duration_seconds=duration,
    )


def _short_prompt(prompt: str) -> str:
    compact = " ".join(prompt.split())
    return compact if len(compact) <= 52 else f"{compact[:49]}..."


__all__ = [
    "DesktopBackend",
    "DesktopJobSummary",
    "ProductionDesktopBackend",
]
