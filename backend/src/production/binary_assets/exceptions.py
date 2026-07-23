"""Typed failures for durable binary production assets."""


class BinaryAssetError(RuntimeError):
    """Base error for provider-independent binary asset infrastructure."""


class BinaryAssetConfigurationError(BinaryAssetError):
    """Storage configuration is unsafe or inconsistent."""


class BinaryAssetPathError(BinaryAssetError):
    """A storage path is outside the contractual workspace location."""


class BinaryAssetLinkError(BinaryAssetPathError):
    """A symbolic link, junction, or unsafe hard link was encountered."""


class BinaryAssetNotFoundError(BinaryAssetError):
    """Registered binary asset bytes are missing."""


class BinaryAssetConflictError(BinaryAssetError):
    """An existing asset is incompatible and must not be overwritten."""


class BinaryAssetSizeError(BinaryAssetError):
    """Binary size is invalid or differs from durable metadata."""


class BinaryAssetHashError(BinaryAssetError):
    """SHA-256 is invalid or differs from durable metadata."""


class BinaryAssetMimeError(BinaryAssetError):
    """Declared MIME, extension, or decoded media type is invalid."""


class BinaryAssetCorruptError(BinaryAssetMimeError):
    """Image bytes cannot be decoded as a complete supported image."""


class BinaryAssetMetadataError(BinaryAssetError):
    """Durable binary metadata is invalid or unsafe."""


class BinaryAssetIOError(BinaryAssetError):
    """A filesystem operation failed without exposing its path."""
