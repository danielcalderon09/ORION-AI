"""Typed failures for media composition planning."""


class MediaCompositionError(Exception):
    """Base failure for media composition."""


class MediaCompositionSourceError(MediaCompositionError):
    """A required durable source is absent or invalid."""


class MediaCompositionPlanError(MediaCompositionError):
    """Source data cannot form a coherent deterministic timeline."""


class MediaCompositionStorageError(MediaCompositionError):
    """Durable plan storage failed."""


class MediaCompositionConflictError(MediaCompositionStorageError):
    """Existing durable state conflicts with this logical plan."""


class MediaCompositionStalePlanError(MediaCompositionError):
    """Durable plan identity no longer matches verified source inputs."""


class MediaCompositionCorruptError(MediaCompositionStorageError):
    """Stored composition data is corrupt or unsafe."""
