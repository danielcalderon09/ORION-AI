"""Safe provider failure taxonomy used by the PlanningHandler."""


class PlanningProviderError(RuntimeError):
    """Base planning provider error containing no provider secrets."""


class PlanningProviderConfigurationError(PlanningProviderError):
    pass


class PlanningProviderDependencyError(PlanningProviderConfigurationError):
    """The selected optional provider support is not installed."""


class PlanningProviderAuthenticationError(PlanningProviderError):
    pass


class PlanningProviderRateLimitError(PlanningProviderError):
    pass


class PlanningProviderTimeoutError(PlanningProviderError):
    pass


class PlanningProviderUnavailableError(PlanningProviderError):
    pass


class PlanningProviderResponseError(PlanningProviderError):
    pass


class PlanningProviderContractError(PlanningProviderError):
    pass
