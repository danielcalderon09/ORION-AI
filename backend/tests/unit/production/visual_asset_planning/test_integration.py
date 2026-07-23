"""SQLite/workspace integration for the durable Phase 5D pipeline."""

import hashlib
import json

import httpx
import pytest
from sqlalchemy import func, select

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.services.models import (
    CreateProductionJobCommand,
)
from backend.src.production.composition.container import build_production_container
from backend.src.production.domain.enums import ArtifactType, ProductionJobStatus
from backend.src.production.infrastructure.persistence.models import (
    ArtifactRecord,
    ProductionBase,
)
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path
from backend.src.production.scene_planning.models import ProductionScenePlan
from backend.src.production.visual_asset_planning.configuration import (
    VisualAssetPlanningConfiguration,
)
from backend.src.production.visual_asset_planning.ports import (
    VisualAssetPlanningProviderRequest,
)
from backend.src.production.visual_asset_planning.providers import (
    SimulatedVisualAssetPlanningProvider,
)
from backend.src.production.visual_asset_planning.providers.openrouter_provider import (
    OpenRouterVisualAssetPlanningProvider,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("visual_provider_name", ["simulated", "openrouter"])
async def test_pipeline_persists_four_linked_json_artifacts_without_network(
    monkeypatch,
    tmp_path,
    visual_provider_name,
) -> None:
    calls = 0

    def visual_factory(**kwargs):
        async def respond(http_request):
            nonlocal calls
            calls += 1
            external = json.loads(http_request.content)
            prompt = json.loads(external["messages"][1]["content"])
            scene_plan = ProductionScenePlan.model_validate(prompt["production_scene_plan"])
            configuration = VisualAssetPlanningConfiguration.model_validate(
                prompt["visual_asset_planning_configuration"]
            )
            generated = await SimulatedVisualAssetPlanningProvider().generate_visual_asset_plan(
                VisualAssetPlanningProviderRequest(
                    job_id="10000000-0000-4000-8000-000000000899",
                    command_id="20000000-0000-4000-8000-000000000899",
                    correlation_id="30000000-0000-4000-8000-000000000899",
                    attempt_number=1,
                    scene_plan=scene_plan,
                    configuration=configuration,
                )
            )
            return httpx.Response(
                200,
                json={
                    "id": "fake-visual-generation",
                    "model": "qwen/fake-visual-planner",
                    "choices": [
                        {
                            "message": {"content": (generated.visual_asset_plan.model_dump_json())},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 2,
                        "total_tokens": 3,
                    },
                },
                request=http_request,
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            base_url="https://openrouter.ai/api/v1",
        )
        kwargs["max_transport_attempts"] = 1
        return OpenRouterVisualAssetPlanningProvider(**kwargs, client=client)

    monkeypatch.setattr(
        "backend.src.production.composition.container."
        "load_openrouter_visual_asset_planning_provider",
        lambda: visual_factory,
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
        ORION_VISUAL_ASSET_PLANNING_PROVIDER=visual_provider_name,
        ORION_VISUAL_ASSET_PLANNING_API_KEY=(
            "fake-only" if visual_provider_name == "openrouter" else None
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
                    "visual_asset_planning": {
                        "target_width": 1080,
                        "target_height": 1920,
                    },
                },
            )
        )
        await container.worker.run_until_idle(max_cycles=40)
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
        expected = {
            ArtifactType.PRODUCTION_PLAN.value,
            ArtifactType.PRODUCTION_SCRIPT.value,
            ArtifactType.PRODUCTION_SCENE_PLAN.value,
            ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN.value,
        }
        durable = {
            artifact_type: (relative_path, checksum, metadata)
            for artifact_type, relative_path, checksum, metadata in rows
            if artifact_type in expected
        }
        assert set(durable) == expected
        for relative_path, checksum, _ in durable.values():
            target = settings.PROJECTS_DIR.joinpath(*relative_path.split("/"))
            assert target.is_file()
            assert hashlib.sha256(target.read_bytes()).hexdigest() == checksum
        source_checksum = durable[ArtifactType.PRODUCTION_SCENE_PLAN.value][1]
        visual_metadata = durable[ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN.value][2]
        assert visual_metadata["source_scene_plan_sha256"] == source_checksum
        assert visual_metadata["asset_count"] == 2
        assert artifact_count == first_artifact_count
        assert calls == (1 if visual_provider_name == "openrouter" else 0)

        public_artifacts = await container.list_artifacts.execute(created.job.job_id)
        visual = next(
            item.artifact
            for item in public_artifacts.items
            if item.artifact.artifact_type is ArtifactType.PRODUCTION_VISUAL_ASSET_PLAN
        )
        assert "visual_asset_plan" not in visual.metadata
        assert visual.size_bytes is not None
    finally:
        await container.aclose()
