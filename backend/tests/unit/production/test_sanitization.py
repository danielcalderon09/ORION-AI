"""Security boundaries for public provider metadata."""

import pytest

from backend.src.production.application.sanitization import (
    UnsafeProductionDataError,
    sanitize_public_json,
    validate_safe_json,
)


def test_token_metrics_are_safe_but_secret_tokens_are_not() -> None:
    metrics = {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    assert validate_safe_json(metrics) == metrics
    for key in ("api_token", "access_token", "authorization_token"):
        with pytest.raises(UnsafeProductionDataError):
            validate_safe_json({key: "secret-value"})
        assert sanitize_public_json({key: "secret-value"})[key] == "[REDACTED]"


def test_openrouter_headers_are_rejected_as_public_configuration() -> None:
    for key in ("authorization", "http_referer", "x_title"):
        with pytest.raises(UnsafeProductionDataError):
            validate_safe_json({key: "private-value"})
