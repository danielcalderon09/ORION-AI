"""Isolated Production API composition."""
# ruff: noqa: E402

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

_IMPORT_HOME = Path(tempfile.gettempdir()) / "orion-phase4-import"
os.environ.setdefault("ORION_HOME", str(_IMPORT_HOME))
os.environ.setdefault("MODELS_DIR", str(_IMPORT_HOME / "models"))
os.environ.setdefault("PROJECTS_DIR", str(_IMPORT_HOME / "projects"))
os.environ.setdefault("TEMP_DIR", str(_IMPORT_HOME / "temp"))

import httpx
import pytest
from fastapi import FastAPI

from backend.src.infrastructure.config.settings import Settings
from backend.src.main import create_app
from backend.src.production.composition.container import (
    ProductionContainer,
    build_production_container,
)
from backend.src.production.infrastructure.persistence.models import ProductionBase
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path


@pytest.fixture
def production_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        ORION_HOME=tmp_path / "home",
        MODELS_DIR=tmp_path / "models",
        PROJECTS_DIR=tmp_path / "projects",
        TEMP_DIR=tmp_path / "temp",
        ORION_DATABASE_URL=sqlite_url_from_path(tmp_path / "production-api.db"),
        ORION_PROMPT_VIDEO_ENABLED=True,
        ORION_PRODUCTION_WORKER_ENABLED=False,
    )


@pytest.fixture
def production_app(
    production_settings: Settings,
) -> AsyncIterator[tuple[FastAPI, ProductionContainer]]:
    app = create_app(production_settings)
    container = build_production_container(production_settings)
    ProductionBase.metadata.create_all(container.engine)
    app.state.production_container = container
    yield app, container
    container.shutdown()


@pytest.fixture
async def production_client(
    production_app: tuple[FastAPI, ProductionContainer],
) -> AsyncIterator[httpx.AsyncClient]:
    app, _ = production_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
