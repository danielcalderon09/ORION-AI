"""Local dependency availability without startup subprocess execution."""

import shutil


def resolve_media_executable(configured: str | None, default: str) -> str:
    """Return configuration verbatim or a PATH-resolved executable name."""

    if configured is not None and configured.strip():
        return configured.strip()
    return shutil.which(default) or default
