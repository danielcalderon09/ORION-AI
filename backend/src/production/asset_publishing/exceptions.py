"""Typed failures for secure asset publication."""


class AssetPublishingError(Exception):
    """Base error for the bounded context."""


class AssetPublishingConfigurationError(AssetPublishingError):
    pass


class AssetPublishingUnavailableError(AssetPublishingError):
    pass


class AssetPublicationConflictError(AssetPublishingError):
    pass


class AssetPublicationNotFoundError(AssetPublishingError):
    pass


class AssetPublicationExpiredError(AssetPublishingError):
    pass


class AssetPublicationUrlError(AssetPublishingError, ValueError):
    pass


class AssetPublicationIntegrityError(AssetPublishingError):
    pass


class PublishedAssetManifestError(AssetPublishingError):
    pass


class PublishedAssetManifestConflictError(PublishedAssetManifestError):
    pass


class PublishedAssetManifestCorruptError(PublishedAssetManifestError):
    pass


class PublishedAssetRecoveryError(AssetPublishingError):
    pass
