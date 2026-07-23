"""Internal configuration for provider-neutral binary asset storage."""

from dataclasses import dataclass, field
from pathlib import Path

from backend.src.production.binary_assets.exceptions import (
    BinaryAssetConfigurationError,
)

DEFAULT_BINARY_ASSET_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)
DEFAULT_BINARY_ASSET_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})


@dataclass(frozen=True, slots=True)
class AssetStorageConfiguration:
    """Private storage policy; it is never accepted from a production job."""

    workspace: Path
    max_asset_size: int = 25_000_000
    allowed_mime_types: frozenset[str] = field(
        default_factory=lambda: DEFAULT_BINARY_ASSET_MIME_TYPES
    )
    allowed_extensions: frozenset[str] = field(
        default_factory=lambda: DEFAULT_BINARY_ASSET_EXTENSIONS
    )

    def __post_init__(self) -> None:
        workspace = self.workspace.expanduser()
        if not workspace.is_absolute():
            workspace = workspace.resolve()
        if workspace.is_symlink():
            raise BinaryAssetConfigurationError(
                "binary asset workspace cannot be a symbolic link"
            )
        if not 1 <= self.max_asset_size <= 250_000_000:
            raise BinaryAssetConfigurationError(
                "binary asset maximum size is outside safe limits"
            )
        mime_types = frozenset(item.strip().lower() for item in self.allowed_mime_types)
        extensions = frozenset(
            item.strip().lower().removeprefix(".")
            for item in self.allowed_extensions
        )
        if not mime_types or not mime_types <= DEFAULT_BINARY_ASSET_MIME_TYPES:
            raise BinaryAssetConfigurationError(
                "binary asset MIME allowlist contains an unsupported type"
            )
        if not extensions or not extensions <= DEFAULT_BINARY_ASSET_EXTENSIONS:
            raise BinaryAssetConfigurationError(
                "binary asset extension allowlist contains an unsupported extension"
            )
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "allowed_mime_types", mime_types)
        object.__setattr__(self, "allowed_extensions", extensions)
