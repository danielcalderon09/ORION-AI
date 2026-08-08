"""Image acquisition settings, composition, API, and lifecycle tests."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.api.schemas import CreateProductionJobRequest
from backend.src.production.composition.container import build_production_container
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionProviderConfigurationException,
)
from backend.src.production.infrastructure.persistence.session import (
    sqlite_url_from_path,
)
from backend.src.production.infrastructure.planning_artifact_reconciler import (
    LocalProductionArtifactReconciler,
)
from backend.tests.unit.production.image_acquisition.conftest import JOB_ID


class RegisteredPaths:
    def __init__(self, paths=()) -> None:
        self.paths = frozenset(paths)

    def list_registered_paths(self):
        return self.paths


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


def test_default_provider_is_simulated_and_first_to_close(tmp_path) -> None:
    configured = settings(tmp_path)
    assert configured.ORION_IMAGE_ACQUISITION_PROVIDER == "simulated"
    container = build_production_container(configured)
    assert type(container.image_acquisition_provider).__name__ == (
        "SimulatedImageAcquisitionProvider"
    )
    assert container.async_resources[1] is container.image_acquisition_provider
    container.shutdown()


@pytest.mark.parametrize("provider", ["unknown", "openai", "openrouter"])
def test_unknown_or_keyless_provider_fails_without_fallback(
    tmp_path,
    provider,
) -> None:
    with pytest.raises(ImageAcquisitionProviderConfigurationException):
        build_production_container(
            settings(
                tmp_path,
                ORION_IMAGE_ACQUISITION_PROVIDER=provider,
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"ORION_IMAGE_ACQUISITION_MODEL": ""},
        {"ORION_IMAGE_ACQUISITION_BASE_URL": "http://openrouter.ai/api/v1"},
        {
            "ORION_IMAGE_ACQUISITION_BASE_URL": (
                "https://user:password@openrouter.ai/api/v1"
            )
        },
        {"ORION_IMAGE_ACQUISITION_BASE_URL": "https:///api/v1"},
    ],
)
def test_openrouter_rejects_invalid_model_or_url(tmp_path, overrides) -> None:
    with pytest.raises(ImageAcquisitionProviderConfigurationException):
        build_production_container(
            settings(
                tmp_path,
                ORION_IMAGE_ACQUISITION_PROVIDER="openrouter",
                ORION_IMAGE_ACQUISITION_API_KEY="fake-test-only",
                **overrides,
            )
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ORION_IMAGE_ACQUISITION_MAX_TRANSPORT_ATTEMPTS", 6),
        ("ORION_IMAGE_ACQUISITION_TIMEOUT_SECONDS", 0),
        ("ORION_IMAGE_ACQUISITION_MAX_RESPONSE_BYTES", 100_000_001),
        ("ORION_IMAGE_ACQUISITION_MAX_DECODED_IMAGE_BYTES", 0),
        ("ORION_IMAGE_ACQUISITION_MAX_PLAN_BYTES", 50_000_001),
        ("ORION_IMAGE_ACQUISITION_MAX_MANIFEST_BYTES", 50_000_001),
        ("ORION_IMAGE_ACQUISITION_PROVIDER_ONLY", "unsafe/provider"),
    ],
)
def test_settings_enforce_safe_limits(tmp_path, name, value) -> None:
    with pytest.raises(ValidationError):
        settings(tmp_path, **{name: value})


@pytest.mark.asyncio
async def test_container_closes_image_provider_first(monkeypatch, tmp_path) -> None:
    close_order = []

    class FakeProvider:
        def __init__(self, **kwargs):
            self.configuration = kwargs

        async def generate_image(self, request):
            raise AssertionError("startup must not invoke the image provider")

        async def close(self):
            close_order.append("image")

    monkeypatch.setattr(
        "backend.src.production.composition.container."
        "load_openrouter_image_acquisition_provider",
        lambda: FakeProvider,
    )
    container = build_production_container(
        settings(
            tmp_path,
            ORION_IMAGE_ACQUISITION_PROVIDER="openrouter",
            ORION_IMAGE_ACQUISITION_API_KEY="fake-test-only",
            ORION_IMAGE_ACQUISITION_MODEL="openai/test-image-model",
            ORION_IMAGE_ACQUISITION_ALLOW_BILLABLE_REQUESTS=True,
            ORION_IMAGE_ACQUISITION_ESTIMATED_COST_USD="0.001",
            ORION_IMAGE_ACQUISITION_MAX_ESTIMATED_COST_USD="0.001",
        )
    )
    assert close_order == []
    await container.aclose()
    assert close_order == ["image"]


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
        "provider_only",
        "output_format",
    ],
)
def test_private_image_configuration_is_rejected(private_key) -> None:
    with pytest.raises(ValidationError):
        CreateProductionJobRequest(
            prompt="Create a safe test video.",
            configuration={
                "image_acquisition": {private_key: "not-allowed"}
            },
        )


@pytest.mark.asyncio
async def test_reconciler_recognizes_only_contractual_manifest(tmp_path) -> None:
    now = datetime(2026, 7, 23, 18, 0, tzinfo=UTC)
    manifest = (
        tmp_path
        / "production"
        / str(JOB_ID)
        / "acquiring_assets"
        / "attempt-1"
        / "image-acquisition-manifest.json"
    )
    unknown = manifest.with_name("unknown.json")
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    unknown.write_text("{}", encoding="utf-8")
    old = (now - timedelta(hours=1)).timestamp()
    os.utime(manifest, (old, old))
    reconciler = LocalProductionArtifactReconciler(
        workspace_root=tmp_path,
        registered_reader=RegisteredPaths(),
        minimum_age_seconds=0,
        action="quarantine",
        clock=lambda: now,
    )
    report = await reconciler.reconcile()
    assert report.scanned == 1
    assert report.quarantined == 1
    assert unknown.is_file()
    assert (
        tmp_path
        / "production-quarantine"
        / str(JOB_ID)
        / "acquiring_assets"
        / "attempt-1"
        / "image-acquisition-manifest.json"
    ).is_file()
