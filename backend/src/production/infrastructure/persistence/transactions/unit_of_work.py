"""Minimal production unit of work sharing one SQLAlchemy session."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from backend.src.production.infrastructure.persistence.repositories import (
    SQLAlchemyArtifactStore,
    SQLAlchemyProductionJobRepository,
)
from backend.src.production.infrastructure.persistence.session import ProductionSessionFactory


class ProductionUnitOfWork:
    """Own one session and expose explicit commit, rollback, and close."""

    def __init__(
        self,
        session_factory: ProductionSessionFactory,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._session: Session | None = None
        self._committed = False
        self.jobs: SQLAlchemyProductionJobRepository
        self.artifacts: SQLAlchemyArtifactStore

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session

    async def __aenter__(self) -> "ProductionUnitOfWork":
        self._session = self._session_factory()
        self.jobs = SQLAlchemyProductionJobRepository(self._session, clock=self._clock)
        self.artifacts = SQLAlchemyArtifactStore(self._session, clock=self._clock)
        return self

    async def commit(self) -> None:
        self.session.commit()
        self._committed = True

    async def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if exc_type is not None or not self._committed:
                await self.rollback()
        finally:
            if self._session is not None:
                self._session.close()
                self._session = None
