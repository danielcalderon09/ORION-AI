"""Typed failures for durable image acquisition."""
# ruff: noqa: N818


class ImageAcquisitionError(RuntimeError):
    """Base image acquisition failure."""


class ImageAcquisitionValidationError(ImageAcquisitionError):
    """Provider-independent contract or mapping is invalid."""


class ImageAcquisitionUnsupportedAssetException(ImageAcquisitionValidationError):
    """The durable visual plan requests a capability not implemented yet."""


class ImageAcquisitionProviderError(ImageAcquisitionError):
    """Base provider failure."""


class ImageAcquisitionProviderDependencyException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderConfigurationException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderAuthenticationException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderRateLimitException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderTimeoutException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderUnavailableException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderResponseException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderContractException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderPolicyException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderModelException(ImageAcquisitionProviderError):
    pass


class ProductionVisualAssetPlanReadError(ImageAcquisitionError):
    pass


class ProductionVisualAssetPlanNotFoundException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanAmbiguousException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanTypeException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanJobException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanPathException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanLinkException(
    ProductionVisualAssetPlanPathException
):
    pass


class ProductionVisualAssetPlanMissingFileException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanSizeException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanChecksumException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanEncodingException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanJsonException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanContractException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanVersionException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanTransientReadException(ProductionVisualAssetPlanReadError):
    pass


class ImageAcquisitionManifestError(ImageAcquisitionError):
    pass


class ImageAcquisitionManifestConflictException(ImageAcquisitionManifestError):
    pass


class ImageAcquisitionManifestCorruptException(ImageAcquisitionManifestError):
    pass
