"""SQLite/workspace integration for the durable Phase 5C pipeline."""

import hashlib
import json

import httpx
import pytest
from sqlalchemy import func, select

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.services.models import CreateProductionJobCommand
from backend.src.production.composition.container import build_production_container
from backend.src.production.domain.enums import ArtifactType, ProductionJobStatus
from backend.src.production.infrastructure.persistence.models import ArtifactRecord, ProductionBase
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path
from backend.src.production.scene_planning.providers.openrouter_provider import (
    OpenRouterScenePlanningProvider,
)
from backend.src.production.scene_planning.providers.simulated_provider import (
    SimulatedScenePlanningProvider,
)
from backend.src.production.scripting.models import ProductionScript


def fake_scene_plan_from_request(request):
    external = json.loads(request.content)
    prompt = json.loads(external["messages"][1]["content"])
    script = ProductionScript.model_validate(prompt["production_script"])
    return script


@pytest.mark.asyncio
@pytest.mark.parametrize("scene_provider_name", ["simulated", "openrouter"])
async def test_pipeline_persists_three_linked_json_artifacts_without_network(
    monkeypatch,
    tmp_path,
    scene_provider_name,
) -> None:
    def scene_factory(**kwargs):
        async def respond(request):
            script = fake_scene_plan_from_request(request)
            generated = await SimulatedScenePlanningProvider().generate_scene_plan(script)
            return httpx.Response(
                200,
                json={
                    "id": "fake-scene-generation",
                    "model": "google/fake-scene-planning",
                    "choices": [
                        {
                            "message": {
                                "content": generated.scene_plan.model_dump_json()
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 2,
                        "total_tokens": 3,
                    },
                },
                request=request,
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            base_url="https://openrouter.ai/api/v1",
        )
        kwargs["max_transport_attempts"] = 1
        return OpenRouterScenePlanningProvider(**kwargs, client=client)

    monkeypatch.setattr(
        "backend.src.production.composition.container.load_openrouter_scene_planning_provider",
        lambda: scene_factory,
    )
    settings = Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_DATABASE_URL=sqlite_url_from_path(tmp_path / "production.db"),
        ORION_PROMPT_VIDEO_ENABLED=True,
        ORION_PRODUCTION_WORKER_ENABLED=False,
        ORION_SCENE_PLANNING_PROVIDER=scene_provider_name,
        ORION_SCENE_PLANNING_API_KEY=(
            "fake-only" if scene_provider_name == "openrouter" else None
        ),
    )
    container = build_production_container(settings)
    ProductionBase.metadata.create_all(container.engine)
    try:
        created = await container.create_job.execute(
            CreateProductionJobCommand(
                prompt="Explain a solar eclipse",
                configuration={
                    "planning": {
                        "language": "en",
                        "target_duration_seconds": 20,
                        "scene_count_hint": 2,
                    },
                    "scripting": {"tone": "calm"},
                },
            )
        )
        await container.worker.run_until_idle(max_cycles=30)
        with container.engine.connect() as connection:
            first_artifact_count = connection.scalar(
                select(func.count(ArtifactRecord.artifact_id)).where(
                    ArtifactRecord.job_id == str(created.job.job_id)
                )
            )
        await container.worker.run_until_idle(max_cycles=5)
        completed = await container.get_job.execute(created.job.job_id)
        assert completed.job.status is ProductionJobStatus.COMPLETED
        with container.engine.connect() as connection:
            rows = connection.execute(
                select(
                    ArtifactRecord.artifact_type,
                    ArtifactRecord.relative_path,
                    ArtifactRecord.sha256,
                    ArtifactRecord.metadata_json,
                ).where(ArtifactRecord.job_id == str(created.job.job_id))
            ).all()
            artifact_count = connection.scalar(
                select(func.count(ArtifactRecord.artifact_id)).where(
                    ArtifactRecord.job_id == str(created.job.job_id)
                )
            )
        durable = {
            artifact_type: (relative_path, checksum, metadata)
            for artifact_type, relative_path, checksum, metadata in rows
            if artifact_type
            in {
                ArtifactType.PRODUCTION_PLAN.value,
                ArtifactType.PRODUCTION_SCRIPT.value,
                ArtifactType.PRODUCTION_SCENE_PLAN.value,
            }
        }
        assert set(durable) == {
            ArtifactType.PRODUCTION_PLAN.value,
            ArtifactType.PRODUCTION_SCRIPT.value,
            ArtifactType.PRODUCTION_SCENE_PLAN.value,
        }
        for relative_path, checksum, _ in durable.values():
            target = settings.PROJECTS_DIR.joinpath(*relative_path.split("/"))
            assert target.is_file()
            assert hashlib.sha256(target.read_bytes()).hexdigest() == checksum
        script_checksum = durable[ArtifactType.PRODUCTION_SCRIPT.value][1]
        scene_metadata = durable[ArtifactType.PRODUCTION_SCENE_PLAN.value][2]
        assert scene_metadata["source_script_sha256"] == script_checksum
        assert scene_metadata["scene_count"] == 2
        assert scene_metadata["shot_count"] == 2
        assert artifact_count is not None
        assert artifact_count == first_artifact_count
    finally:
        await container.aclose()
