"""Settings, composition, lifecycle, and reconciliation contracts."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.composition.container import build_production_container
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path
from backend.src.production.infrastructure.planning_artifact_reconciler import (
    LocalProductionArtifactReconciler,
)
from backend.src.production.scene_planning.exceptions import (
    ScenePlanningProviderConfigurationException,
)
from backend.tests.unit.production.scene_planning.conftest import JOB_ID


def settings(tmp_path, **overrides):
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


def test_scene_planning_defaults_to_simulated(tmp_path) -> None:
    configured = settings(tmp_path)
    assert configured.ORION_SCENE_PLANNING_PROVIDER == "simulated"
    container = build_production_container(configured)
    assert type(container.scene_planning_provider).__name__ == (
        "SimulatedScenePlanningProvider"
    )
    assert container.async_resources[3] is container.scene_planning_provider
    container.shutdown()


@pytest.mark.parametrize("provider", ["unknown", "openai", "openrouter"])
def test_unknown_or_keyless_provider_fails_without_fallback(
    tmp_path, provider
) -> None:
    with pytest.raises(ScenePlanningProviderConfigurationException):
        build_production_container(
            settings(tmp_path, ORION_SCENE_PLANNING_PROVIDER=provider)
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"ORION_SCENE_PLANNING_MODEL": ""},
        {"ORION_SCENE_PLANNING_BASE_URL": "http://openrouter.ai/api/v1"},
        {
            "ORION_SCENE_PLANNING_BASE_URL": (
                "https://user:password@openrouter.ai/api/v1"
            )
        },
        {"ORION_SCENE_PLANNING_BASE_URL": "https:///api/v1"},
    ],
)
def test_openrouter_rejects_invalid_model_or_url(tmp_path, overrides) -> None:
    with pytest.raises(ScenePlanningProviderConfigurationException):
        build_production_container(
            settings(
                tmp_path,
                ORION_SCENE_PLANNING_PROVIDER="openrouter",
                ORION_SCENE_PLANNING_API_KEY="fake-only",
                **overrides,
            )
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ORION_SCENE_PLANNING_MAX_TRANSPORT_ATTEMPTS", 6),
        ("ORION_SCENE_PLANNING_TEMPERATURE", 3),
        ("ORION_SCENE_PLANNING_MAX_SCRIPT_BYTES", 0),
        ("ORION_SCENE_PLANNING_MAX_PLAN_BYTES", 50_000_001),
    ],
)
def test_scene_planning_setting_limits(tmp_path, name, value) -> None:
    with pytest.raises(ValidationError):
        settings(tmp_path, **{name: value})


@pytest.mark.asyncio
async def test_container_closes_scene_planning_provider(monkeypatch, tmp_path) -> None:
    instances = []

    class FakeProvider:
        def __init__(self, **kwargs):
            self.closed = False
            instances.append(self)

        async def generate_scene_plan(self, script):
            raise AssertionError("startup must not call the provider")

        async def close(self):
            self.closed = True

    monkeypatch.setattr(
        "backend.src.production.composition.container.load_openrouter_scene_planning_provider",
        lambda: FakeProvider,
    )
    container = build_production_container(
        settings(
            tmp_path,
            ORION_SCENE_PLANNING_PROVIDER="openrouter",
            ORION_SCENE_PLANNING_API_KEY="fake-only",
        )
    )
    assert len(instances) == 1 and not instances[0].closed
    await container.aclose()
    assert instances[0].closed


class RegisteredReader:
    def list_registered_paths(self):
        return frozenset()


@pytest.mark.asyncio
async def test_reconciler_quarantines_only_contractual_scene_plan(tmp_path) -> None:
    now = datetime(2026, 7, 22, 16, 0, tzinfo=UTC)
    relative = f"production/{JOB_ID}/scene_planning/attempt-1/scene-plan.json"
    target = tmp_path.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    timestamp = (now - timedelta(seconds=600)).timestamp()
    os.utime(target, (timestamp, timestamp))
    unrelated = target.parent / "other.json"
    unrelated.write_text("{}", encoding="utf-8")
    reconciler = LocalProductionArtifactReconciler(
        workspace_root=tmp_path,
        registered_reader=RegisteredReader(),
        minimum_age_seconds=300,
        action="quarantine",
        clock=lambda: now,
    )
    report = await reconciler.reconcile()
    assert report.quarantined == 1
    assert not target.exists()
    assert unrelated.exists()
