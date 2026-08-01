"""SQLite/workspace integration for the durable simulated Phase 5B pipeline."""

import hashlib
import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.services.models import CreateProductionJobCommand
from backend.src.production.composition.container import build_production_container
from backend.src.production.domain.enums import ArtifactType, ProductionJobStatus
from backend.src.production.infrastructure.persistence.models import ArtifactRecord, ProductionBase
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path
from backend.src.production.planning.providers.openrouter_provider import (
    OpenRouterPlanningProvider,
)
from backend.src.production.scripting.providers.openrouter_provider import (
    OpenRouterScriptingProvider,
)


def fake_openrouter_body(payload, *, model):
    return {
        "id": "fake-generation-id",
        "model": model,
        "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


def fake_plan_payload():
    return {
        "schema_version": "1.0.0",
        "title": "Solar eclipse",
        "summary": "A durable fake plan.",
        "language": "en",
        "target_duration_seconds": 20,
        "aspect_ratio": "9:16",
        "visual_style": "cinematic",
        "narrative_style": "engaging",
        "scenes": [
            {
                "scene_number": index,
                "title": f"Scene {index}",
                "narration": f"Useful narration explains eclipse scene number {index} clearly.",
                "visual_description": f"Visual {index}",
                "image_prompt": f"Image {index}",
                "motion_instruction": "Slow zoom",
                "estimated_duration_seconds": 10,
                "transition": "cut",
                "on_screen_text": None,
                "metadata": {},
            }
            for index in (1, 2)
        ],
        "metadata": {},
    }


def fake_script_from_request(request):
    external = json.loads(request.content)
    prompt = json.loads(external["messages"][1]["content"])
    plan = prompt["source_plan"]
    return {
        "schema_version": "1.0.0",
        "source_plan_schema_version": plan["schema_version"],
        "title": plan["title"],
        "language": plan["language"],
        "target_duration_seconds": plan["target_duration_seconds"],
        "tone": prompt["configuration"]["tone"],
        "opening_hook": plan["summary"],
        "closing_call_to_action": None,
        "scenes": [
            {
                "scene_number": index,
                "source_scene_number": scene["scene_number"],
                "heading": scene["title"],
                "narration": scene["narration"],
                "estimated_duration_seconds": scene["estimated_duration_seconds"],
                "delivery_style": "calm",
                "pronunciation_notes": [],
                "on_screen_text": scene["on_screen_text"],
                "visual_intent": scene["visual_description"],
                "transition_note": scene["transition"],
                "metadata": {},
            }
            for index, scene in enumerate(plan["scenes"], start=1)
        ],
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_fully_simulated_pipeline_persists_plan_and_script(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_DATABASE_URL=sqlite_url_from_path(tmp_path / "production.db"),
        ORION_PROMPT_VIDEO_ENABLED=True,
        ORION_PRODUCTION_WORKER_ENABLED=False,
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
        durable = {
            artifact_type: (relative_path, checksum, metadata)
            for artifact_type, relative_path, checksum, metadata in rows
            if artifact_type
            in {
                ArtifactType.PRODUCTION_PLAN.value,
                ArtifactType.PRODUCTION_SCRIPT.value,
            }
        }
        assert set(durable) == {
            ArtifactType.PRODUCTION_PLAN.value,
            ArtifactType.PRODUCTION_SCRIPT.value,
        }
        for relative_path, checksum, _ in durable.values():
            target = settings.PROJECTS_DIR.joinpath(*relative_path.split("/"))
            assert target.is_file()
            assert hashlib.sha256(target.read_bytes()).hexdigest() == checksum
        plan_checksum = durable[ArtifactType.PRODUCTION_PLAN.value][1]
        script_metadata = durable[ArtifactType.PRODUCTION_SCRIPT.value][2]
        assert script_metadata["source_plan_sha256"] == plan_checksum
        assert script_metadata["scene_count"] == 2
    finally:
        await container.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("planning_name", "scripting_name"),
    [
        ("openrouter", "simulated"),
        ("simulated", "openrouter"),
        ("openrouter", "openrouter"),
    ],
)
async def test_fake_real_provider_combinations_complete_without_network(
    monkeypatch, tmp_path, planning_name, scripting_name
) -> None:
    def planning_factory(**kwargs):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=fake_openrouter_body(fake_plan_payload(), model="openai/fake-planning"),
                    request=request,
                )
            ),
            base_url="https://openrouter.ai/api/v1",
        )
        kwargs["max_transport_attempts"] = 1
        return OpenRouterPlanningProvider(
            **kwargs,
            client=client,
            owns_client=True,
        )

    def scripting_factory(**kwargs):
        def respond(request):
            return httpx.Response(
                200,
                json=fake_openrouter_body(
                    fake_script_from_request(request), model="anthropic/fake-scripting"
                ),
                request=request,
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            base_url="https://openrouter.ai/api/v1",
        )
        kwargs["max_transport_attempts"] = 1
        return OpenRouterScriptingProvider(
            **kwargs,
            client=client,
            owns_client=True,
        )

    monkeypatch.setattr(
        "backend.src.production.composition.container.load_openrouter_planning_provider",
        lambda: planning_factory,
    )
    monkeypatch.setattr(
        "backend.src.production.composition.container.load_openrouter_scripting_provider",
        lambda: scripting_factory,
    )
    settings = Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_DATABASE_URL=sqlite_url_from_path(tmp_path / "combinations.db"),
        ORION_PROMPT_VIDEO_ENABLED=True,
        ORION_PRODUCTION_WORKER_ENABLED=False,
        ORION_PLANNING_PROVIDER=planning_name,
        ORION_PLANNING_API_KEY="fake-only" if planning_name == "openrouter" else None,
        ORION_SCRIPTING_PROVIDER=scripting_name,
        ORION_SCRIPTING_API_KEY="fake-only" if scripting_name == "openrouter" else None,
        ORION_SCRIPTING_MODEL="fake/scripting" if scripting_name == "openrouter" else "",
        ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS=scripting_name == "openrouter",
        ORION_SCRIPTING_ESTIMATED_COST_USD=(
            Decimal("0.01") if scripting_name == "openrouter" else None
        ),
        ORION_SCRIPTING_MAX_ESTIMATED_COST_USD=(
            Decimal("0.10") if scripting_name == "openrouter" else None
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
        completed = await container.get_job.execute(created.job.job_id)
        assert completed.job.status is ProductionJobStatus.COMPLETED
    finally:
        await container.aclose()
