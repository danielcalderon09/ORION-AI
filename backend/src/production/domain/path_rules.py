"""Cross-platform validation for workspace-relative artifact paths."""

from pathlib import PurePosixPath, PureWindowsPath


def validate_relative_path(value: str) -> str:
    """Return a safe relative path or raise ``ValueError``.

    Both Windows and POSIX semantics are checked regardless of the host OS so
    a contract cannot smuggle an unsafe path to another runtime.
    """

    if not value or not value.strip():
        raise ValueError("path must not be empty")
    if "\x00" in value:
        raise ValueError("path must not contain NUL bytes")

    normalized = value.strip()
    windows_path = PureWindowsPath(normalized)
    posix_path = PurePosixPath(normalized)
    if windows_path.is_absolute() or windows_path.drive or posix_path.is_absolute():
        raise ValueError("path must be relative")

    parts = normalized.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise ValueError("path traversal is not allowed")
    if any(part in {"", "."} for part in parts):
        raise ValueError("path must use normalized relative segments")
    return normalized
