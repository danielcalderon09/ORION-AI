"""Controlled Production runtime lifecycle tests."""

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.services.exceptions import (
    ProductionRuntimeUnavailableError,
)
from backend.src.production.composition.lifecycle import (
    start_production_runtime,
    stop_production_runtime,
)
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path


def _migrated_settings(tmp_path, *, worker_enabled: bool) -> Settings:
    url = sqlite_url_from_path(tmp_path / "lifecycle.db")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    return Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_DATABASE_URL=url,
        ORION_PROMPT_VIDEO_ENABLED=True,
        ORION_PRODUCTION_WORKER_ENABLED=worker_enabled,
        ORION_PRODUCTION_POLL_INTERVAL_SECONDS=0.01,
        ORION_PRODUCTION_SHUTDOWN_TIMEOUT_SECONDS=1,
    )


@pytest.mark.asyncio
async def test_startup_and_shutdown_own_resources(tmp_path) -> None:
    settings = _migrated_settings(tmp_path, worker_enabled=True)
    app = FastAPI()
    await start_production_runtime(app, settings)
    task = app.state.production_worker_task
    assert task is not None
    assert task.done() is False
    with pytest.raises(ProductionRuntimeUnavailableError):
        await start_production_runtime(app, settings)
    await stop_production_runtime(app, settings)
    assert task.done()
    assert not hasattr(app.state, "production_container")


@pytest.mark.asyncio
async def test_worker_disabled_starts_no_task(tmp_path) -> None:
    settings = _migrated_settings(tmp_path, worker_enabled=False)
    app = FastAPI()
    before = set(asyncio.all_tasks())
    await start_production_runtime(app, settings)
    assert app.state.production_worker_task is None
    assert set(asyncio.all_tasks()) == before
    await stop_production_runtime(app, settings)


@pytest.mark.asyncio
async def test_missing_schema_fails_and_cleans_partial_container(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_DATABASE_URL=sqlite_url_from_path(tmp_path / "unmigrated.db"),
        ORION_PROMPT_VIDEO_ENABLED=True,
    )
    app = FastAPI()
    with pytest.raises(ProductionRuntimeUnavailableError, match="not migrated"):
        await start_production_runtime(app, settings)
    assert not hasattr(app.state, "production_container")
