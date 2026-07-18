"""Explicit Alembic revision policy for the Production runtime."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.services.exceptions import (
    ProductionRuntimeUnavailableError,
)

PRODUCTION_ALEMBIC_REVISION = "20260718_0003"


def _alembic_config(database_url: str) -> Config:
    root = Path(__file__).resolve().parents[4]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


async def ensure_production_schema(settings: Settings, engine: Engine) -> None:
    if settings.ORION_PRODUCTION_AUTO_MIGRATE:
        if not settings.ORION_DATABASE_URL:
            raise ProductionRuntimeUnavailableError(
                "ORION_PRODUCTION_AUTO_MIGRATE requires an explicit ORION_DATABASE_URL"
            )
        await asyncio.to_thread(
            command.upgrade,
            _alembic_config(settings.production_database_url),
            "head",
        )
        return
    await asyncio.to_thread(_validate_revision, engine)


def _validate_revision(engine: Engine) -> None:
    if "alembic_version" not in inspect(engine).get_table_names():
        raise ProductionRuntimeUnavailableError(
            "Production schema is not migrated; run Alembic upgrade head or enable "
            "ORION_PRODUCTION_AUTO_MIGRATE with an explicit database URL"
        )
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != PRODUCTION_ALEMBIC_REVISION:
        raise ProductionRuntimeUnavailableError(
            f"Production schema revision {revision!r} does not match "
            f"{PRODUCTION_ALEMBIC_REVISION}"
        )
