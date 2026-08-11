"""Safe SCRIPTING reader and provider error taxonomies."""


class ProductionPlanReadError(RuntimeError):
    """A durable production plan could not be read safely."""


class ProductionPlanNotFoundError(ProductionPlanReadError):
    pass


class ProductionPlanPathError(ProductionPlanReadError):
    pass


class ProductionPlanMissingFileError(ProductionPlanReadError):
    pass


class ProductionPlanIntegrityError(ProductionPlanReadError):
    pass


class ProductionPlanSizeError(ProductionPlanReadError):
    pass


class ProductionPlanChecksumError(ProductionPlanIntegrityError):
    pass


class ProductionPlanEncodingError(ProductionPlanReadError):
    pass


class ProductionPlanJsonError(ProductionPlanReadError):
    pass


class ProductionPlanContractError(ProductionPlanReadError):
    pass


class ProductionPlanVersionError(ProductionPlanReadError):
    pass


class ProductionPlanTransientReadError(ProductionPlanReadError):
    pass


class ScriptingProviderError(RuntimeError):
    """Base provider error that never contains credentials or raw responses."""


class ScriptingProviderConfigurationError(ScriptingProviderError):
    pass


class ScriptingProviderDependencyError(ScriptingProviderConfigurationError):
    pass


class ScriptingProviderAuthenticationError(ScriptingProviderError):
    pass


class ScriptingProviderRateLimitError(ScriptingProviderError):
    pass


class ScriptingProviderTimeoutError(ScriptingProviderError):
    pass


class ScriptingProviderUnavailableError(ScriptingProviderError):
    pass


class ScriptingProviderUncertainError(ScriptingProviderError):
    """A possibly billable submission cannot be safely repeated automatically."""


class ScriptingProviderResponseError(ScriptingProviderError):
    pass


class ScriptingProviderContractError(ScriptingProviderError):
    pass


class ScriptingProviderDurationPolicyExhaustedError(ScriptingProviderContractError):
    """The bounded narration correction budget was exhausted."""


class ScriptingProviderDurationPolicyBudgetError(ScriptingProviderConfigurationError):
    """A duration-policy retry was blocked by the job billable budget."""
