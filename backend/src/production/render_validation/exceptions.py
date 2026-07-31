"""Typed failures for final render validation."""


class FinalRenderValidationError(Exception):
    """Base final-render validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FinalRenderSourceError(FinalRenderValidationError):
    """Required registered render state is absent, stale, or corrupt."""


class FinalRenderStorageError(FinalRenderValidationError):
    """Durable final-validation storage failed."""


class FinalRenderConflictError(FinalRenderStorageError):
    """Write-once or compare-and-swap state conflicted."""


class FinalRenderCorruptError(FinalRenderStorageError):
    """Existing final-validation state is corrupt."""
