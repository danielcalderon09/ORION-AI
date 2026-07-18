from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[5]


def test_runtime_lease_migration_upgrade_downgrade_upgrade(tmp_path) -> None:
    database = tmp_path / "runtime-migration.db"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "src" / "infrastructure" / "persistence" / "sqlite" / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    assert "production_leases" in inspect(engine).get_table_names()
    engine.dispose()
    command.downgrade(config, "20260717_0001")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    tables = inspect(engine).get_table_names()
    assert "production_leases" not in tables
    assert "production_jobs" in tables
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    assert "production_leases" in inspect(engine).get_table_names()
    engine.dispose()
