"""Temporary SQLite fixtures for production persistence tests."""

import pytest

from backend.src.production.infrastructure.persistence.models import ProductionBase
from backend.src.production.infrastructure.persistence.session import (
    create_production_engine,
    create_production_session_factory,
    sqlite_url_from_path,
)


@pytest.fixture
def production_database(tmp_path):
    database_path = tmp_path / "production-test.db"
    engine = create_production_engine(sqlite_url_from_path(database_path))
    ProductionBase.metadata.create_all(engine)
    session_factory = create_production_session_factory(engine)
    try:
        yield engine, session_factory
    finally:
        engine.dispose()
