"""Filesystem isolation shared by legacy integration suites."""

from pathlib import Path

import pytest

from backend.src.infrastructure.config.settings import settings


@pytest.fixture(autouse=True)
def isolate_integration_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent integration tests from reading or writing the user workspace."""

    roots = {
        "ORION_HOME": tmp_path / "orion-home",
        "PROJECTS_DIR": tmp_path / "projects",
        "TEMP_DIR": tmp_path / "temp",
        "MODELS_DIR": tmp_path / "models",
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    for name, path in roots.items():
        monkeypatch.setattr(settings, name, path)
