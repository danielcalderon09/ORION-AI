"""Optional dependency metadata and lazy availability tests."""

import importlib
import sys
import tomllib
from pathlib import Path

import pytest

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.planning.exceptions import PlanningProviderDependencyError
from backend.src.production.planning.providers.availability import (
    OPENROUTER_DEPENDENCY_MESSAGE,
    load_openrouter_planning_provider,
)

ROOT = Path(__file__).parents[5]


def test_package_declares_bounded_provider_extras() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = configuration["project"]["optional-dependencies"]
    assert extras["planning-openai"] == ["httpx>=0.27,<1.0"]
    assert extras["production-llm"] == ["httpx>=0.27,<1.0"]
    assert extras["production-openrouter"] == extras["production-llm"]
    package_finder = configuration["tool"]["setuptools"]["packages"]["find"]
    assert package_finder == {
        "where": ["."],
        "include": ["backend", "backend.src", "backend.src.*"],
    }


def test_missing_http_dependency_is_actionable_and_safe() -> None:
    def missing_httpx(name: str):
        raise ModuleNotFoundError("blocked optional dependency", name="httpx")

    with pytest.raises(PlanningProviderDependencyError) as captured:
        load_openrouter_planning_provider(importer=missing_httpx)
    assert str(captured.value) == OPENROUTER_DEPENDENCY_MESSAGE
    assert "production-llm" in str(captured.value)
    assert "blocked optional dependency" not in str(captured.value)


def test_unrelated_import_failure_is_not_hidden() -> None:
    def missing_internal(name: str):
        raise ModuleNotFoundError("internal module missing", name="internal_module")

    with pytest.raises(ModuleNotFoundError, match="internal module missing"):
        load_openrouter_planning_provider(importer=missing_internal)


def test_simulated_provider_package_does_not_load_http_adapter(monkeypatch) -> None:
    sys.modules.pop(
        "backend.src.production.planning.providers.openrouter_provider",
        None,
    )
    package = importlib.reload(
        importlib.import_module("backend.src.production.planning.providers")
    )
    assert package.SimulatedPlanningProvider is not None
    assert "backend.src.production.planning.providers.openrouter_provider" not in sys.modules


def test_flag_false_does_not_validate_optional_provider(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_PROMPT_VIDEO_ENABLED=False,
        ORION_PLANNING_PROVIDER="openrouter",
    )
    assert settings.ORION_PROMPT_VIDEO_ENABLED is False
    assert settings.ORION_PLANNING_PROVIDER == "openrouter"
