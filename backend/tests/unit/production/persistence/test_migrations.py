"""Alembic migration tests against an isolated SQLite file."""

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from backend.src.production.infrastructure.persistence.session import (
    create_production_engine,
    sqlite_url_from_path,
)

PRODUCTION_TABLES = {
    "production_jobs",
    "production_stage_runs",
    "stage_commands",
    "stage_results",
    "production_events",
    "production_artifacts",
}


def alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def test_upgrade_downgrade_and_reupgrade_preserve_historical_tables(tmp_path) -> None:
    database_url = sqlite_url_from_path(tmp_path / "migration-test.db")
    engine = create_production_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE historical_clips (id TEXT PRIMARY KEY)"))
    config = alembic_config(database_url)

    command.upgrade(config, "head")
    upgraded_tables = set(inspect(engine).get_table_names())
    assert PRODUCTION_TABLES.issubset(upgraded_tables)
    assert "historical_clips" in upgraded_tables

    command.downgrade(config, "base")
    downgraded_tables = set(inspect(engine).get_table_names())
    assert PRODUCTION_TABLES.isdisjoint(downgraded_tables)
    assert "historical_clips" in downgraded_tables

    command.upgrade(config, "head")
    reupgraded_tables = set(inspect(engine).get_table_names())
    assert PRODUCTION_TABLES.issubset(reupgraded_tables)
    assert "historical_clips" in reupgraded_tables
    engine.dispose()
