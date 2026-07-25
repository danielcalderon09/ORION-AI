"""Private global settings for temporary asset publishing."""

from pathlib import Path

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
        return validate_public_https_url(value.rstrip("/"))
