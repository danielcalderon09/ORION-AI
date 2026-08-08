"""Desktop provider status is local, bounded, and contains no secret."""

import pytest

from backend.src.desktop.backend_client import ProductionDesktopBackend
from backend.src.infrastructure.config.settings import Settings


@pytest.mark.asyncio
async def test_provider_status_uses_only_safe_local_labels(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_OPENROUTER_API_KEY="fake-key-never-real",
        ORION_SCRIPTING_PROVIDER="openrouter",
        ORION_SCRIPTING_MODEL="fake/model",
        ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS=True,
        ORION_SCRIPTING_ESTIMATED_COST_USD="0.001",
        ORION_SCRIPTING_MAX_ESTIMATED_COST_USD="0.001",
    )
    backend = ProductionDesktopBackend(settings_factory=lambda: settings)
    status = await backend.provider_status()
    assert status.scripting == "OpenRouter"
    assert status.images == "Simulated"
    assert status.voice == "Simulated"
    assert status.video == "Simulated"
    assert status.music == "Simulated"
    assert "fake-key-never-real" not in repr(status)
