"""Security validation for provider-produced planning text."""

import re
from pathlib import PurePosixPath, PureWindowsPath

_UNSAFE_PATTERNS = (
    re.compile(r"<\s*(script|iframe|object|embed)\b", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"\b(?:cmd\.exe|powershell|bash\s+-c|sh\s+-c)\b", re.IGNORECASE),
    re.compile(r"\b(?:rm\s+-rf|del\s+/[sq]|format\s+[a-z]:)\b", re.IGNORECASE),
    re.compile(r"(?:\.\./|\.\.\\)"),
)


def validate_planning_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("planning text must not be empty")
    windows = PureWindowsPath(normalized)
    posix = PurePosixPath(normalized)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise ValueError("absolute paths are not allowed in planning text")
    if any(pattern.search(normalized) for pattern in _UNSAFE_PATTERNS):
        raise ValueError("unsafe executable or path instruction in planning text")
    return normalized
