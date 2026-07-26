"""Persistence-specific errors that do not depend on HTTP concerns."""

from backend.src.production.application.services.exceptions import (
    ProductionConcurrencyError as ApplicationConcurrencyError,
)
from backend.src.production.application.services.exceptions import (
    ProductionRecordIntegrityError as ApplicationRecordIntegrityError,
)


class ProductionPersistenceError(RuntimeError):
    """Base error for production persistence failures."""


class ProductionConcurrencyError(ProductionPersistenceError, ApplicationConcurrencyError):
    """Raised when optimistic locking detects stale state."""


class ProductionIdempotencyConflictError(ProductionPersistenceError):
    """Raised when a durable identity is reused with different content."""


class ProductionEventSequenceError(ProductionPersistenceError):
    """Raised when a job's durable event sequence is invalid."""


class ProductionRecordIntegrityError(
    ProductionPersistenceError,
    ApplicationRecordIntegrityError,
):
    """Raised when a record cannot safely reconstruct a contract."""
