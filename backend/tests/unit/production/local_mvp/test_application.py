from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.application.services.models import (
    ProductionArtifactPage,
    ProductionEventPage,
    ProductionJobView,
)
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.local_mvp import (
    LocalMvpApplication,
    LocalMvpRequest,
    local_mvp_profile,
)
from backend.src.production.runtime.runtime_models import WorkerRunResult

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
JOB_ID = UUID("90000000-0000-4000-8000-000000000001")


class FakeGetJob:
    def __init__(self, job: ProductionJob) -> None:
        self.job = job

    async def execute(self, job_id: UUID) -> ProductionJobView:
        assert job_id == self.job.job_id
        return ProductionJobView(job=self.job, row_version=1)


class FakeCreateJob:
    async def execute(self, command):
        raise AssertionError(f"create must not run during resume: {command}")


class FakeArtifacts:
    async def execute(self, job_id: UUID) -> ProductionArtifactPage:
        assert job_id == JOB_ID
        return ProductionArtifactPage(items=())


class FakeEvents:
    async def execute(self, job_id: UUID) -> ProductionEventPage:
        assert job_id == JOB_ID
        return ProductionEventPage(items=())


class StalledWorker:
    def __init__(self, get_job: FakeGetJob) -> None:
        self.get_job = get_job
        self.calls = 0

    async def run_once(self) -> WorkerRunResult:
        self.calls += 1
        return WorkerRunResult(
            processed=True,
            job_id=self.get_job.job.job_id,
            previous_status=self.get_job.job.status,
            updated_status=self.get_job.job.status,
            updated_stage=self.get_job.job.current_stage,
        )


def _job(status: ProductionJobStatus = ProductionJobStatus.RUNNING) -> ProductionJob:
    return ProductionJob(
        job_id=JOB_ID,
        prompt="Explica tres curiosidades sobre Marte.",
        status=status,
        current_stage=ProductionStage.PLANNING,
        created_at=NOW,
        updated_at=NOW,
    )


def test_profile_defaults_are_small_vertical_and_deterministic() -> None:
    first = local_mvp_profile()
    second = local_mvp_profile()
    assert first == second
    assert first["planning"] == {
        "language": "es",
        "target_duration_seconds": 8,
        "aspect_ratio": "9:16",
        "visual_style": "cinematic",
        "narrative_style": "engaging",
        "scene_count_hint": 2,
    }
    visual = first["visual_asset_planning"]
    assert isinstance(visual, dict)
    assert (visual["target_width"], visual["target_height"]) == (576, 1024)


def test_profile_accepts_one_scene_for_a_bounded_provider_smoke_test() -> None:
    profile = local_mvp_profile(scene_count_hint=1)

    planning = profile["planning"]
    assert isinstance(planning, dict)
    assert planning["scene_count_hint"] == 1


@pytest.mark.parametrize("prompt", [None, "", "   \n "])
def test_new_job_rejects_empty_prompt(prompt: str | None) -> None:
    with pytest.raises(ValidationError, match="prompt must not be empty"):
        LocalMvpRequest(prompt=prompt)


def test_resume_rejects_a_second_prompt() -> None:
    with pytest.raises(ValidationError, match="cannot be combined"):
        LocalMvpRequest(prompt="idea", resume_job_id=JOB_ID)


def test_application_rejects_dry_run_mode(tmp_path) -> None:
    get_job = FakeGetJob(_job())
    with pytest.raises(ValueError, match="requires the ffmpeg renderer"):
        LocalMvpApplication(
            create_job=FakeCreateJob(),  # type: ignore[arg-type]
            get_job=get_job,  # type: ignore[arg-type]
            list_artifacts=FakeArtifacts(),  # type: ignore[arg-type]
            list_events=FakeEvents(),  # type: ignore[arg-type]
            worker=StalledWorker(get_job),  # type: ignore[arg-type]
            workspace_root=tmp_path,
            configured_renderer="dry_run",
        )


@pytest.mark.asyncio
async def test_bounded_loop_preserves_job_and_reports_limit(tmp_path) -> None:
    get_job = FakeGetJob(_job())
    worker = StalledWorker(get_job)
    app = LocalMvpApplication(
        create_job=FakeCreateJob(),  # type: ignore[arg-type]
        get_job=get_job,  # type: ignore[arg-type]
        list_artifacts=FakeArtifacts(),  # type: ignore[arg-type]
        list_events=FakeEvents(),  # type: ignore[arg-type]
        worker=worker,  # type: ignore[arg-type]
        workspace_root=tmp_path,
        configured_renderer="ffmpeg",
    )
    report = await app.run(
        LocalMvpRequest(resume_job_id=JOB_ID),
        max_stage_iterations=3,
    )
    assert report.success is False
    assert report.status is ProductionJobStatus.RUNNING
    assert report.failure is not None
    assert report.failure.error_code == "local_iteration_limit_reached"
    assert report.cost_summary is not None
    assert report.cost_summary.total_accounted_cost_usd == "0"
    assert report.cost_summary.reported_cost_coverage_percent == "100.00"
    assert report.cost_summary.fully_reported is True
    assert worker.calls == 3
    assert get_job.job.status is ProductionJobStatus.RUNNING


@pytest.mark.asyncio
async def test_failed_resume_is_not_reset_or_executed(tmp_path) -> None:
    get_job = FakeGetJob(_job(ProductionJobStatus.FAILED))
    worker = StalledWorker(get_job)
    app = LocalMvpApplication(
        create_job=FakeCreateJob(),  # type: ignore[arg-type]
        get_job=get_job,  # type: ignore[arg-type]
        list_artifacts=FakeArtifacts(),  # type: ignore[arg-type]
        list_events=FakeEvents(),  # type: ignore[arg-type]
        worker=worker,  # type: ignore[arg-type]
        workspace_root=tmp_path,
        configured_renderer="ffmpeg",
    )
    report = await app.run(LocalMvpRequest(resume_job_id=JOB_ID))
    assert report.success is False
    assert report.status is ProductionJobStatus.FAILED
    assert worker.calls == 0
