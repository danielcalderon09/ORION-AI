"""Private global settings for temporary asset publishing."""

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from backend.src.production.asset_publishing.url_validation import (
    validate_public_https_url,
)
from backend.src.production.domain.base import ContractModel


class AssetPublishingConfiguration(ContractModel):
    publisher: str = Field(default="null", pattern=r"^(null|filesystem)$")
    public_root: Path
    public_base_url: str = "https://assets.orion.test"
    lifetime_seconds: int = Field(default=900, ge=30, le=86_400)
    max_asset_bytes: int = Field(default=250_000_000, ge=1, le=250_000_000)
    max_manifest_bytes: int = Field(default=4_000_000, ge=1, le=16_000_000)

    @field_validator("public_base_url")
    @classmethod
    def safe_base_url(cls, value: str) -> str:
        normalized = validate_public_https_url(value.rstrip("/"))
        if urlsplit(normalized).query:
            raise ValueError("asset publication base URL cannot contain a query")
        return normalized

    @field_validator("public_root")
    @classmethod
    def dedicated_root(cls, value: Path) -> Path:
        return validate_dedicated_publication_root(value)


def validate_dedicated_publication_root(
    value: Path,
    *,
    forbidden_roots: tuple[Path, ...] = (),
) -> Path:
    """Reject broad roots while allowing a dedicated child directory."""

    root = value.expanduser().resolve(strict=False)
    repository_root = _repository_root()
    forbidden = {
        Path.home().resolve(strict=False),
        (Path.home() / "Desktop").resolve(strict=False),
        Path(root.anchor).resolve(strict=False),
        *(candidate.expanduser().resolve(strict=False) for candidate in forbidden_roots),
    }
    if repository_root is not None:
        forbidden.add(repository_root)
    if root in forbidden:
        raise ValueError("asset publication root must be a dedicated subdirectory")
    return root


def _repository_root() -> Path | None:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".git").exists():
            return candidate
    return None
