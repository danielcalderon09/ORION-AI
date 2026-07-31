"""Typed failures for local render preparation."""


class RenderingError(Exception):
    """Base rendering preparation failure."""


class RenderingConfigurationError(RenderingError):
    """The local renderer configuration is unsupported."""


class RenderingRequestError(RenderingError):
    """The deterministic request is invalid."""


class RenderingValidationError(RenderingError):
    """The selected renderer rejected a request or output."""


class RenderingExecutableError(RenderingError):
    """A configured local media executable is missing or invalid."""


class RenderingProcessError(RenderingError):
    """A controlled local media process failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RenderingProcessTimeoutError(RenderingProcessError):
    """A controlled local media process exceeded its deadline."""


class RenderingStorageError(RenderingError):
    """Durable local storage failed."""


class RenderingConflictError(RenderingStorageError):
    """Durable content or a compare-and-swap checkpoint conflicted."""


class RenderingCorruptError(RenderingStorageError):
    """Durable render preparation state is corrupt."""


class RenderingStaleSourceError(RenderingError):
    """Durable render state belongs to another composition-plan identity."""


class RenderingUnexpectedOutputError(RenderingConflictError):
    """The uncreated future output path is already occupied."""


class RenderingSourceError(RenderingError):
    """A registered Phase 5H.2 source could not be verified."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
