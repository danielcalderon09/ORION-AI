"""Application errors independent of HTTP and storage details."""


class ProductionApplicationError(RuntimeError):
    """Base error for production application use cases."""


class ProductionJobNotFoundError(ProductionApplicationError):
    pass


class ProductionJobConflictError(ProductionApplicationError):
    pass


class ProductionJobStateError(ProductionApplicationError):
    pass


class ProductionRequestIdConflictError(ProductionApplicationError):
    pass


class ProductionValidationError(ProductionApplicationError):
    pass


class ProductionRuntimeUnavailableError(ProductionApplicationError):
    pass


class ProductionConcurrencyError(ProductionApplicationError):
    """Raised when an application write loses an optimistic concurrency race."""


class ProductionRecordIntegrityError(ProductionApplicationError):
    """Raised when durable state cannot safely satisfy an application contract."""
