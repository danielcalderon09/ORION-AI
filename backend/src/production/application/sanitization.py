"""Deterministic validation and redaction for public production JSON."""

import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

SENSITIVE_PARTS = ("api_key", "credential", "password", "secret", "token")


class UnsafeProductionDataError(ValueError):
    """Raised when input JSON contains credentials or absolute paths."""


def validate_safe_json(value: Any, *, path: str = "root") -> Any:
    json.dumps(value, allow_nan=False, sort_keys=True)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in SENSITIVE_PARTS):
                raise UnsafeProductionDataError(f"sensitive key is not allowed: {path}.{key}")
            result[str(key)] = validate_safe_json(child, path=f"{path}.{key}")
        return result
    if isinstance(value, list):
        return [validate_safe_json(item, path=f"{path}[]") for item in value]
    if isinstance(value, str):
        windows = PureWindowsPath(value)
        posix = PurePosixPath(value)
        if windows.is_absolute() or windows.drive or posix.is_absolute():
            raise UnsafeProductionDataError(f"absolute path is not allowed: {path}")
    return value


def sanitize_public_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if any(part in str(key).lower() for part in SENSITIVE_PARTS)
            else sanitize_public_json(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list):
        return [sanitize_public_json(item) for item in value]
    return value
