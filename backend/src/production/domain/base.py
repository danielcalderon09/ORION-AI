"""Shared configuration for production contract models."""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Strict, immutable base for versioned production contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )
