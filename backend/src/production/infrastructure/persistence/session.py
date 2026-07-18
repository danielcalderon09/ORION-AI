"""Explicit engine and session construction for production persistence."""

from pathlib import Path
from typing import Any, TypeAlias

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

ProductionSessionFactory: TypeAlias = sessionmaker[Session]


def sqlite_url_from_path(path: Path) -> str:
    """Build a portable SQLAlchemy URL without interpolating path text."""

    resolved = path.expanduser().resolve()
    return URL.create("sqlite+pysqlite", database=str(resolved)).render_as_string(
        hide_password=False
    )


def create_production_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create an engine; never creates tables or opens the global ORION database."""

    url = make_url(database_url)
    connect_args: dict[str, Any] = {}
    if url.get_backend_name() == "sqlite":
        connect_args = {"check_same_thread": False, "timeout": 5.0}

    engine = create_engine(
        url,
        echo=echo,
        future=True,
        connect_args=connect_args,
    )
    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection: Any, connection_record: Any) -> None:
            del connection_record
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()

    return engine


def create_production_session_factory(engine: Engine) -> ProductionSessionFactory:
    """Create sessions with explicit transaction and commit boundaries."""

    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )
