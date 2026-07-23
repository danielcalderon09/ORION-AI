"""Visual asset planning input, validation, and provider exceptions."""


class VisualAssetPlanningException(RuntimeError):  # noqa: N818 - public contract name
    """Base exception for the VISUAL_ASSET_PLANNING capability."""


class VisualAssetPlanningValidationException(VisualAssetPlanningException):
    pass


class ProductionScenePlanReadException(VisualAssetPlanningException):
    pass


class ProductionScenePlanNotFoundException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanAmbiguousException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanTypeException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanPathException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanSymlinkException(ProductionScenePlanPathException):
    pass


class ProductionScenePlanMissingFileException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanIntegrityException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanSizeException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanChecksumException(ProductionScenePlanIntegrityException):
    pass


class ProductionScenePlanEncodingException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanJsonException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanContractException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanVersionException(ProductionScenePlanReadException):
    pass


class ProductionScenePlanTransientReadException(ProductionScenePlanReadException):
    pass


class VisualAssetPlanningProviderException(VisualAssetPlanningException):
    pass


class VisualAssetPlanningProviderDependencyException(VisualAssetPlanningProviderException):
    pass


class VisualAssetPlanningProviderConfigurationException(VisualAssetPlanningProviderException):
    pass


class VisualAssetPlanningProviderAuthenticationException(VisualAssetPlanningProviderException):
    pass


class VisualAssetPlanningProviderRateLimitException(VisualAssetPlanningProviderException):
    pass


class VisualAssetPlanningProviderTimeoutException(VisualAssetPlanningProviderException):
    pass


class VisualAssetPlanningProviderUnavailableException(VisualAssetPlanningProviderException):
    pass


class VisualAssetPlanningProviderResponseException(VisualAssetPlanningProviderException):
    pass


class VisualAssetPlanningProviderContractException(VisualAssetPlanningProviderException):
    pass


class VisualAssetPlanningStructuredOutputException(VisualAssetPlanningProviderException):
    """The selected model/provider cannot honor strict Structured Outputs."""
