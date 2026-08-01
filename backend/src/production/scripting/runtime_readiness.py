"""Local-only readiness checks for SCRIPTING provider selection."""

from enum import StrEnum

from backend.src.production.domain.base import ContractModel
from backend.src.production.scripting.exceptions import (
    ScriptingProviderConfigurationError,
)


class ScriptingRuntimeReadinessCode(StrEnum):
    """Closed, non-secret outcomes for local scripting configuration checks."""

    SIMULATED_READY = "simulated_ready"
    OPENROUTER_READY = "openrouter_ready"
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    OPENROUTER_API_KEY_MISSING = "openrouter_api_key_missing"
    OPENROUTER_MODEL_MISSING = "openrouter_model_missing"


class ScriptingRuntimeReadiness(ContractModel):
    """Safe readiness result that contains no credential value."""

    configured_provider: str
    ready: bool
    code: ScriptingRuntimeReadinessCode
    message: str
    api_key_configured: bool
    model_configured: bool


def assess_scripting_runtime_readiness(
    *,
    provider: str,
    api_key_configured: bool,
    model: str,
) -> ScriptingRuntimeReadiness:
    """Assess local configuration without reading a secret or invoking a provider."""

    normalized_provider = provider.strip().lower()
    model_configured = bool(model.strip())
    if normalized_provider == "simulated":
        return ScriptingRuntimeReadiness(
            configured_provider="simulated",
            ready=True,
            code=ScriptingRuntimeReadinessCode.SIMULATED_READY,
            message="Simulated scripting provider is active.",
            api_key_configured=api_key_configured,
            model_configured=model_configured,
        )
    if normalized_provider != "openrouter":
        return ScriptingRuntimeReadiness(
            configured_provider=normalized_provider,
            ready=False,
            code=ScriptingRuntimeReadinessCode.UNSUPPORTED_PROVIDER,
            message=f"Unsupported scripting provider: {normalized_provider!r}.",
            api_key_configured=api_key_configured,
            model_configured=model_configured,
        )
    if not api_key_configured:
        return ScriptingRuntimeReadiness(
            configured_provider="openrouter",
            ready=False,
            code=ScriptingRuntimeReadinessCode.OPENROUTER_API_KEY_MISSING,
            message=("OpenRouter scripting is not ready: ORION_SCRIPTING_API_KEY is missing."),
            api_key_configured=False,
            model_configured=model_configured,
        )
    if not model_configured:
        return ScriptingRuntimeReadiness(
            configured_provider="openrouter",
            ready=False,
            code=ScriptingRuntimeReadinessCode.OPENROUTER_MODEL_MISSING,
            message="OpenRouter scripting is not ready: ORION_SCRIPTING_MODEL is empty.",
            api_key_configured=True,
            model_configured=False,
        )
    return ScriptingRuntimeReadiness(
        configured_provider="openrouter",
        ready=True,
        code=ScriptingRuntimeReadinessCode.OPENROUTER_READY,
        message="OpenRouter scripting configuration is locally ready.",
        api_key_configured=True,
        model_configured=True,
    )


def require_scripting_runtime_readiness(
    *,
    provider: str,
    api_key_configured: bool,
    model: str,
) -> ScriptingRuntimeReadiness:
    """Return local readiness or raise a sanitized configuration error."""

    readiness = assess_scripting_runtime_readiness(
        provider=provider,
        api_key_configured=api_key_configured,
        model=model,
    )
    if not readiness.ready:
        raise ScriptingProviderConfigurationError(readiness.message)
    return readiness


__all__ = [
    "ScriptingRuntimeReadiness",
    "ScriptingRuntimeReadinessCode",
    "assess_scripting_runtime_readiness",
    "require_scripting_runtime_readiness",
]
