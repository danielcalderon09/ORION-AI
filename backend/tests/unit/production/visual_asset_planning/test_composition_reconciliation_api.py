"""Settings, composition, lifecycle, API input, and reconciliation contracts."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.api.schemas import CreateProductionJobRequest
from backend.src.production.composition.container import build_production_container
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path
from backend.src.production.infrastructure.planning_artifact_reconciler import (
    LocalProductionArtifactReconciler,
)
from backend.src.production.visual_asset_planning.exceptions import (
    VisualAssetPlanningProviderConfigurationException,
)
from backend.tests.unit.production.visual_asset_planning.conftest import JOB_ID


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


def test_visual_asset_planning_defaults_to_simulated(tmp_path) -> None:
    configured = settings(tmp_path)
    assert configured.ORION_VISUAL_ASSET_PLANNING_PROVIDER == "simulated"
    container = build_production_container(configured)
    assert type(container.visual_asset_planning_provider).__name__ == (
        "SimulatedVisualAssetPlanningProvider"
    )
    assert container.async_resources[1] is container.visual_asset_planning_provider
    container.shutdown()


@pytest.mark.parametrize("provider", ["unknown", "openai", "openrouter"])
def test_unknown_or_keyless_provider_fails_without_fallback(
    tmp_path,
    provider,
) -> None:
    with pytest.raises(VisualAssetPlanningProviderConfigurationException):
        build_production_container(
            settings(
                tmp_path,
                ORION_VISUAL_ASSET_PLANNING_PROVIDER=provider,
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"ORION_VISUAL_ASSET_PLANNING_MODEL": ""},
        {"ORION_VISUAL_ASSET_PLANNING_BASE_URL": ("http://openrouter.ai/api/v1")},
        {"ORION_VISUAL_ASSET_PLANNING_BASE_URL": ("https://user:password@openrouter.ai/api/v1")},
        {"ORION_VISUAL_ASSET_PLANNING_BASE_URL": "https:///api/v1"},
    ],
)
def test_openrouter_rejects_invalid_model_or_url(tmp_path, overrides) -> None:
    with pytest.raises(VisualAssetPlanningProviderConfigurationException):
        build_production_container(
            settings(
                tmp_path,
                ORION_VISUAL_ASSET_PLANNING_PROVIDER="openrouter",
                ORION_VISUAL_ASSET_PLANNING_API_KEY="fake-only",
                **overrides,
            )
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ORION_VISUAL_ASSET_PLANNING_MAX_TRANSPORT_ATTEMPTS", 6),
        ("ORION_VISUAL_ASSET_PLANNING_TEMPERATURE", 3),
        ("ORION_VISUAL_ASSET_PLANNING_MAX_SCENE_PLAN_BYTES", 0),
        ("ORION_VISUAL_ASSET_PLANNING_MAX_ARTIFACT_BYTES", 50_000_001),
    ],
)
def test_visual_asset_planning_setting_limits(tmp_path, name, value) -> None:
    with pytest.raises(ValidationError):
        settings(tmp_path, **{name: value})


@pytest.mark.asyncio
async def test_container_closes_visual_provider_first(monkeypatch, tmp_path) -> None:
    close_order = []

    class FakeProvider:
        def __init__(self, **kwargs):
            self.configuration = kwargs

        async def generate_visual_asset_plan(self, request):
            raise AssertionError("startup must not call provider")

        async def close(self):
            close_order.append("visual")

    monkeypatch.setattr(
        "backend.src.production.composition.container."
        "load_openrouter_visual_asset_planning_provider",
        lambda: FakeProvider,
    )
    container = build_production_container(
        settings(
            tmp_path,
            ORION_VISUAL_ASSET_PLANNING_PROVIDER="openrouter",
            ORION_VISUAL_ASSET_PLANNING_API_KEY="fake-only",
        )
    )
    await container.aclose()
    assert close_order == ["visual"]


@pytest.mark.parametrize(
    "private_key",
    [
        "provider",
        "model",
        "api_key",
        "base_url",
        "headers",
        "timeout",
        "retries",
        "system_prompt",
        "path",
    ],
)
def test_public_api_rejects_private_or_unknown_job_configuration(
    private_key,
) -> None:
    with pytest.raises(ValidationError):
        CreateProductionJobRequest(
            prompt="Safe prompt",
            configuration={"visual_asset_planning": {private_key: "not-allowed"}},
        )


class RegisteredReader:
    def list_registered_paths(self):
        return frozenset()


@pytest.mark.asyncio
async def test_reconciler_quarantines_only_contractual_visual_plan(
    tmp_path,
) -> None:
    now = datetime(2026, 7, 23, 16, 0, tzinfo=UTC)
    relative = f"production/{JOB_ID}/visual_asset_planning/attempt-1/visual-asset-plan.json"
    target = tmp_path.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    timestamp = (now - timedelta(seconds=600)).timestamp()
    os.utime(target, (timestamp, timestamp))
    unrelated = target.parent / "unknown.json"
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
