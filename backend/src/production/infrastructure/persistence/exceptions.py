"""Persistence-specific errors that do not depend on HTTP concerns."""


class ProductionPersistenceError(RuntimeError):
    """Base error for production persistence failures."""


class ProductionConcurrencyError(ProductionPersistenceError):
    """Raised when optimistic locking detects stale state."""


class ProductionIdempotencyConflictError(ProductionPersistenceError):
    """Raised when a durable identity is reused with different content."""


class ProductionEventSequenceError(ProductionPersistenceError):
    """Raised when a job's durable event sequence is invalid."""


class ProductionRecordIntegrityError(ProductionPersistenceError):
    """Raised when a record cannot safely reconstruct a contract."""
