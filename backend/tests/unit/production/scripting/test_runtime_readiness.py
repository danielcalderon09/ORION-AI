"""Network-free scripting runtime readiness checks."""

import pytest

from backend.src.production.scripting.exceptions import (
    ScriptingProviderConfigurationError,
)
from backend.src.production.scripting.runtime_readiness import (
    ScriptingRuntimeReadinessCode,
    assess_scripting_runtime_readiness,
    require_scripting_runtime_readiness,
)


def test_openrouter_api_key_absent_reports_clear_local_error() -> None:
    readiness = assess_scripting_runtime_readiness(
        provider="openrouter",
        api_key_configured=False,
        model="google/gemini-2.5-flash-lite",
    )

    assert readiness.ready is False
    assert readiness.code is ScriptingRuntimeReadinessCode.OPENROUTER_API_KEY_MISSING
    assert readiness.api_key_configured is False
    assert "ORION_SCRIPTING_API_KEY is missing" in readiness.message
    with pytest.raises(ScriptingProviderConfigurationError, match="API_KEY is missing"):
        require_scripting_runtime_readiness(
            provider="openrouter",
            api_key_configured=False,
            model="google/gemini-2.5-flash-lite",
        )


def test_incorrect_provider_is_rejected_without_fallback() -> None:
    readiness = assess_scripting_runtime_readiness(
        provider="other",
        api_key_configured=False,
        model="",
    )

    assert readiness.ready is False
    assert readiness.code is ScriptingRuntimeReadinessCode.UNSUPPORTED_PROVIDER
    with pytest.raises(ScriptingProviderConfigurationError, match="Unsupported"):
        require_scripting_runtime_readiness(
            provider="other",
            api_key_configured=False,
            model="",
        )


def test_openrouter_empty_model_is_rejected_locally() -> None:
    readiness = assess_scripting_runtime_readiness(
        provider="openrouter",
        api_key_configured=True,
        model="   ",
    )

    assert readiness.ready is False
    assert readiness.code is ScriptingRuntimeReadinessCode.OPENROUTER_MODEL_MISSING
    assert readiness.api_key_configured is True
    assert readiness.model_configured is False


def test_correct_openrouter_configuration_is_locally_ready_without_secret_value() -> None:
    readiness = require_scripting_runtime_readiness(
        provider="openrouter",
        api_key_configured=True,
        model="google/gemini-2.5-flash-lite",
    )

    assert readiness.ready is True
    assert readiness.code is ScriptingRuntimeReadinessCode.OPENROUTER_READY
    assert readiness.configured_provider == "openrouter"
    assert readiness.api_key_configured is True
    assert readiness.model_configured is True
