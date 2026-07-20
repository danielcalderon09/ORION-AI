"""Feature flag registration and import-time safety."""

import asyncio

from backend.src.infrastructure.config.settings import Settings
from backend.src.main import create_app


def _settings(tmp_path, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_PROMPT_VIDEO_ENABLED=enabled,
    )


def test_flag_controls_routes_and_does_not_build_container(tmp_path) -> None:
    disabled = create_app(_settings(tmp_path / "off", False))
    enabled = create_app(_settings(tmp_path / "on", True))
    disabled_paths = set(disabled.openapi()["paths"])
    enabled_paths = set(enabled.openapi()["paths"])
    assert "/api/v1/production/jobs" not in disabled_paths
    assert "/api/v1/production/jobs" in enabled_paths
    assert not hasattr(disabled.state, "production_container")
    assert not hasattr(enabled.state, "production_container")


def test_import_and_app_creation_do_not_start_tasks(tmp_path) -> None:
    async def inspect() -> int:
        before = set(asyncio.all_tasks())
        create_app(_settings(tmp_path, True))
        return len(set(asyncio.all_tasks()) - before)

    assert asyncio.run(inspect()) == 0


def test_main_flag_off_does_not_validate_real_provider_credentials(tmp_path) -> None:
    configured = _settings(tmp_path, False).model_copy(
        update={"ORION_PLANNING_PROVIDER": "openrouter", "ORION_PLANNING_API_KEY": None}
    )
    app = create_app(configured)
    assert "/api/v1/production/jobs" not in app.openapi()["paths"]
    assert not hasattr(app.state, "production_container")


def test_planning_provider_defaults_to_simulated(tmp_path) -> None:
    assert _settings(tmp_path, True).ORION_PLANNING_PROVIDER == "simulated"
