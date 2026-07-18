"""Request-id migration is additive and reversible in temporary SQLite."""

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from backend.src.production.infrastructure.persistence.session import (
    create_production_engine,
    sqlite_url_from_path,
)


def test_request_id_migration_upgrade_downgrade_upgrade(tmp_path) -> None:
    url = sqlite_url_from_path(tmp_path / "request-id-migration.db")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_production_engine(url)
    columns = {item["name"] for item in inspect(engine).get_columns("production_jobs")}
    assert {"client_request_id", "request_fingerprint"}.issubset(columns)
    command.downgrade(config, "20260717_0002")
    columns = {item["name"] for item in inspect(engine).get_columns("production_jobs")}
    assert "client_request_id" not in columns
    command.upgrade(config, "head")
    columns = {item["name"] for item in inspect(engine).get_columns("production_jobs")}
    assert "client_request_id" in columns
    engine.dispose()
