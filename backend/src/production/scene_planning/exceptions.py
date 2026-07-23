"""Scene-planning input, validation, and provider exceptions."""


class ScenePlanningException(RuntimeError):  # noqa: N818 - required public contract name
    pass


class ScenePlanningValidationException(ScenePlanningException):
    pass


class ProductionScriptReadException(ScenePlanningException):
    pass


class ProductionScriptNotFoundException(ProductionScriptReadException):
    pass


class ProductionScriptPathException(ProductionScriptReadException):
    pass


class ProductionScriptMissingFileException(ProductionScriptReadException):
    pass


class ProductionScriptIntegrityException(ProductionScriptReadException):
    pass


class ProductionScriptSizeException(ProductionScriptReadException):
    pass


class ProductionScriptChecksumException(ProductionScriptIntegrityException):
    pass


class ProductionScriptEncodingException(ProductionScriptReadException):
    pass


class ProductionScriptJsonException(ProductionScriptReadException):
    pass


class ProductionScriptContractException(ProductionScriptReadException):
    pass


class ProductionScriptVersionException(ProductionScriptReadException):
    pass


class ProductionScriptTransientReadException(ProductionScriptReadException):
    pass


class ScenePlanningProviderException(ScenePlanningException):
    pass


class ScenePlanningProviderDependencyException(ScenePlanningProviderException):
    pass


class ScenePlanningProviderConfigurationException(ScenePlanningProviderException):
    pass


class ScenePlanningProviderAuthenticationException(ScenePlanningProviderException):
    pass


class ScenePlanningProviderRateLimitException(ScenePlanningProviderException):
    pass


class ScenePlanningProviderTimeoutException(ScenePlanningProviderException):
    pass


class ScenePlanningProviderUnavailableException(ScenePlanningProviderException):
    pass


class ScenePlanningProviderResponseException(ScenePlanningProviderException):
    pass


class ScenePlanningProviderContractException(ScenePlanningProviderException):
    pass
