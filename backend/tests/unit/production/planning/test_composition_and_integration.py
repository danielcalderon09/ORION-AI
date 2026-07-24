"""Composition selection and durable fake-real planning integration."""

import hashlib
import json
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.composition.container import build_production_container
from backend.src.production.domain.enums import ArtifactType, ProductionJobStatus
from backend.src.production.infrastructure.persistence.models import ArtifactRecord, ProductionBase
from backend.src.production.infrastructure.persistence.session import (
    create_production_engine,
    create_production_session_factory,
    sqlite_url_from_path,
)
from backend.src.production.planning.artifact_writer import LocalPlanningArtifactWriter
from backend.src.production.planning.exceptions import (
    PlanningProviderConfigurationError,
    PlanningProviderDependencyError,
)
from backend.src.production.planning.prompt_builder import PlanningPromptBuilder
from backend.src.production.planning.providers.openrouter_provider import (
    OpenRouterPlanningProvider,
)
from backend.src.production.runtime import ProductionExecutor, create_handler_registry
from backend.src.production.runtime.handlers import PlanningHandler
from backend.tests.unit.production.runtime.conftest import (
    MutableClock,
    TestVideoClipBoundaryHandler,
    UUIDSequence,
    build_worker,
)
from backend.tests.unit.production.runtime.test_worker import enqueue_job, job_status


@pytest.fixture
def runtime_database(tmp_path):
    engine = create_production_engine(sqlite_url_from_path(tmp_path / "runtime.db"))
    ProductionBase.metadata.create_all(engine)
    sessions = create_production_session_factory(engine)
    try:
        yield engine, sessions
    finally:
        engine.dispose()


def settings(tmp_path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "ORION_HOME": tmp_path / "home",
        "MODELS_DIR": tmp_path / "models",
        "PROJECTS_DIR": tmp_path / "projects",
        "TEMP_DIR": tmp_path / "temp",
        "ORION_DATABASE_URL": sqlite_url_from_path(tmp_path / "composition.db"),
        "ORION_PROMPT_VIDEO_ENABLED": True,
        "ORION_PRODUCTION_WORKER_ENABLED": False,
    }
    values.update(overrides)
    return Settings(**values)


def fake_response_payload() -> dict:
    plan = {
        "schema_version": "1.0.0",
        "title": "Fake real plan",
        "summary": "Validated through the real adapter",
        "language": "es",
        "target_duration_seconds": 60,
        "aspect_ratio": "9:16",
        "visual_style": "cinematic",
        "narrative_style": "engaging",
        "scenes": [
            {
                "scene_number": index,
                "title": f"Scene {index}",
                "narration": "Narration",
                "visual_description": "Visual",
                "image_prompt": "Safe image",
                "motion_instruction": "Slow zoom",
                "estimated_duration_seconds": 15,
                "transition": "cut",
                "on_screen_text": None,
                "metadata": {},
            }
            for index in range(1, 5)
        ],
        "metadata": {},
    }
    return {
        "id": "generation-fake",
        "model": "openai/fake-model",
        "choices": [{"message": {"content": json.dumps(plan)}, "finish_reason": "stop"}],
        "usage": {},
    }


def test_composition_defaults_to_simulated_without_key(tmp_path) -> None:
    container = build_production_container(settings(tmp_path))
    assert type(container.planning_provider).__name__ == "SimulatedPlanningProvider"
    container.shutdown()


@pytest.mark.parametrize("provider", ["unknown", "openai", "openrouter"])
def test_invalid_real_configuration_fails_safely(tmp_path, provider: str) -> None:
    with pytest.raises(PlanningProviderConfigurationError) as captured:
        build_production_container(settings(tmp_path, ORION_PLANNING_PROVIDER=provider))
    assert "API" not in str(captured.value)


def test_missing_optional_dependency_has_no_simulated_fallback(
    monkeypatch,
    tmp_path,
) -> None:
    def unavailable():
        raise PlanningProviderDependencyError(
            "OpenRouter planning support is not installed. Install the production-llm extra."
        )

    monkeypatch.setattr(
        "backend.src.production.composition.container.load_openrouter_planning_provider",
        unavailable,
    )
    configured = settings(
        tmp_path,
        ORION_PLANNING_PROVIDER="openrouter",
        ORION_PLANNING_API_KEY="not-a-real-key",
    )
    with pytest.raises(PlanningProviderDependencyError, match="production-llm"):
        build_production_container(configured)


def test_missing_dependency_disposes_partially_created_engine(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeEngine:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    engine = FakeEngine()

    def unavailable():
        raise PlanningProviderDependencyError(
            "OpenRouter planning support is not installed. Install the production-llm extra."
        )

    monkeypatch.setattr(
        "backend.src.production.composition.container.create_production_engine",
        lambda *args, **kwargs: engine,
    )
    monkeypatch.setattr(
        "backend.src.production.composition.container.create_production_session_factory",
        lambda value: object(),
    )
    monkeypatch.setattr(
        "backend.src.production.composition.container.load_openrouter_planning_provider",
        unavailable,
    )
    configured = settings(
        tmp_path,
        ORION_PLANNING_PROVIDER="openrouter",
        ORION_PLANNING_API_KEY="not-a-real-key",
    )
    with pytest.raises(PlanningProviderDependencyError):
        build_production_container(configured)
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_pipeline_with_fake_real_provider_persists_matching_file(
    runtime_database,
    tmp_path,
) -> None:
    _, session_factory = runtime_database
    clock, uuids = MutableClock(), UUIDSequence(5)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=fake_response_payload(), request=request
            )
        ),
        base_url="https://openrouter.ai/api/v1",
    )
    provider = OpenRouterPlanningProvider(
        api_key="fake-only",
        model="openai/fake-model",
        prompt_builder=PlanningPromptBuilder(),
        client=client,
        max_transport_attempts=1,
    )
    planning_handler = PlanningHandler(
        provider=provider,
        artifact_writer=LocalPlanningArtifactWriter(tmp_path),
        clock=clock,
        uuid_factory=uuids,
    )
    executor = ProductionExecutor(
        create_handler_registry(
            planning_handler=planning_handler,
            video_clip_generation_handler=TestVideoClipBoundaryHandler(
                clock=clock,
                uuid_factory=uuids,
            ),
            clock=clock,
            uuid_factory=uuids,
        )
    )
    job_id = UUID("10000000-0000-4000-8000-000000000599")
    await enqueue_job(session_factory, clock, uuids, job_id)
    worker = build_worker(
        session_factory,
        clock,
        uuids,
        owner_id="fake-real-worker",
        executor=executor,
    )
    await worker.run_until_idle(max_cycles=30)
    assert job_status(session_factory, job_id) is ProductionJobStatus.COMPLETED
    with session_factory() as session:
        record = session.scalar(
            select(ArtifactRecord).where(
                ArtifactRecord.job_id == str(job_id),
                ArtifactRecord.artifact_type == ArtifactType.PRODUCTION_PLAN.value,
            )
        )
        assert record is not None
        target = tmp_path.joinpath(*record.relative_path.split("/"))
        assert target.is_file()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == record.sha256
    await provider.close()
