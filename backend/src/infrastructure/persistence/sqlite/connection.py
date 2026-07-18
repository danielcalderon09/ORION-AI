"""SQLite database connection and session management."""

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()


class Database:
    """Manages SQLite connection for Orion AI."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def create_tables(self) -> None:
        """Create all tables."""
        Base.metadata.create_all(bind=self.engine)

    def get_session(self):
        """Get a new database session."""
        return self.SessionLocal()
