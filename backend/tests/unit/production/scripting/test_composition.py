"""Scripting settings, lazy selection, and packaging contracts."""

import tomllib
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.api.schemas import CreateProductionJobRequest
from backend.src.production.composition.container import build_production_container
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path
from backend.src.production.scripting.exceptions import (
    ScriptingProviderConfigurationError,
    ScriptingProviderDependencyError,
)

ROOT = Path(__file__).resolve().parents[5]

OPENROUTER_SETTINGS = {
    "ORION_SCRIPTING_PROVIDER": "openrouter",
    "ORION_SCRIPTING_API_KEY": "fake-only",
    "ORION_SCRIPTING_MODEL": "fake/scripting",
    "ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS": True,
    "ORION_SCRIPTING_ESTIMATED_COST_USD": Decimal("0.01"),
    "ORION_SCRIPTING_MAX_ESTIMATED_COST_USD": Decimal("0.10"),
}


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


def test_scripting_defaults_and_optional_extra_are_compatible(tmp_path) -> None:
    configured = settings(tmp_path)
    assert configured.ORION_SCRIPTING_PROVIDER == "simulated"
    assert configured.ORION_SCRIPTING_MODEL == ""
    assert configured.ORION_SCRIPTING_API_KEY is None
    assert configured.ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS is False
    assert configured.ORION_SCRIPTING_MAX_PLAN_BYTES == 1_000_000
    container = build_production_container(configured)
    assert type(container.scripting_provider).__name__ == "SimulatedScriptingProvider"
    container.shutdown()
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]
    assert extras["planning-openai"] == extras["production-openai"]
    assert extras["production-llm"] == extras["production-openrouter"]


def test_openrouter_is_selected_only_with_every_explicit_gate(tmp_path) -> None:
    configured = settings(tmp_path, **OPENROUTER_SETTINGS)
    container = build_production_container(configured)
    try:
        assert type(container.scripting_provider).__name__ == "OpenRouterScriptingProvider"
        assert container.openrouter_scripting_request_store is not None
        assert container.openrouter_scripting_reconciler is not None
    finally:
        container.shutdown()


def test_scripting_key_is_runtime_only_secret_and_empty_is_rejected(tmp_path) -> None:
    configured = settings(tmp_path, ORION_SCRIPTING_API_KEY="")
    assert configured.ORION_SCRIPTING_API_KEY is None
    protected = settings(tmp_path, ORION_SCRIPTING_API_KEY="secret-test-value")
    assert "secret-test-value" not in repr(protected)
    assert "secret-test-value" not in protected.model_dump_json()


@pytest.mark.parametrize("provider", ["unknown", "openai", "openrouter"])
def test_unknown_or_keyless_provider_fails_without_fallback(tmp_path, provider) -> None:
    with pytest.raises((ScriptingProviderConfigurationError, ValidationError)):
        build_production_container(settings(tmp_path, ORION_SCRIPTING_PROVIDER=provider))


def test_missing_optional_dependency_fails_safely(monkeypatch, tmp_path) -> None:
    def unavailable():
        raise ScriptingProviderDependencyError("install production-llm")

    monkeypatch.setattr(
        "backend.src.production.composition.container.load_openrouter_scripting_provider",
        unavailable,
    )
    with pytest.raises(ScriptingProviderDependencyError, match="production-llm"):
        build_production_container(
            settings(
                tmp_path,
                **OPENROUTER_SETTINGS,
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"ORION_SCRIPTING_MODEL": ""},
        {"ORION_SCRIPTING_BASE_URL": "http://openrouter.ai/api/v1"},
        {"ORION_SCRIPTING_BASE_URL": "https://user:password@openrouter.ai/api/v1"},
        {"ORION_SCRIPTING_BASE_URL": "https:///api/v1"},
        {"ORION_SCRIPTING_BASE_URL": "https://example.test/api/v1"},
        {"ORION_SCRIPTING_BASE_URL": "https://openrouter.ai/api/v1?redirect=1"},
    ],
)
def test_openrouter_rejects_invalid_model_or_url_before_loading(tmp_path, overrides) -> None:
    with pytest.raises(ScriptingProviderConfigurationError):
        build_production_container(settings(tmp_path, **{**OPENROUTER_SETTINGS, **overrides}))


@pytest.mark.parametrize(
    "missing",
    [
        "ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS",
        "ORION_SCRIPTING_ESTIMATED_COST_USD",
        "ORION_SCRIPTING_MAX_ESTIMATED_COST_USD",
    ],
)
def test_openrouter_missing_billable_gate_fails_closed(tmp_path, missing) -> None:
    values = dict(OPENROUTER_SETTINGS)
    values[missing] = False if missing.endswith("REQUESTS") else None
    with pytest.raises(ScriptingProviderConfigurationError):
        build_production_container(settings(tmp_path, **values))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS", 6),
        ("ORION_SCRIPTING_TEMPERATURE", 3),
        ("ORION_SCRIPTING_MAX_PLAN_BYTES", 0),
        ("ORION_SCRIPTING_MAX_SCRIPT_BYTES", 50_000_001),
    ],
)
def test_scripting_setting_limits_are_enforced(tmp_path, name, value) -> None:
    with pytest.raises(ValidationError):
        settings(tmp_path, **{name: value})


@pytest.mark.parametrize(
    "private_key",
    ["provider", "model", "api_key", "base_url", "http_referer", "x_title"],
)
def test_http_configuration_rejects_private_scripting_keys(private_key) -> None:
    valid = CreateProductionJobRequest(
        prompt="safe",
        configuration={"planning": {}, "scripting": {"tone": "calm"}},
    )
    assert valid.configuration["scripting"] == {"tone": "calm"}
    with pytest.raises(ValidationError):
        CreateProductionJobRequest(
            prompt="safe",
            configuration={"scripting": {private_key: "private-value"}},
        )
