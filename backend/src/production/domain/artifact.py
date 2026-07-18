"""Artifact registry contract."""

from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ArtifactStatus, ArtifactType
from backend.src.production.domain.path_rules import validate_relative_path


class Artifact(ContractModel):
    """Immutable metadata for a file produced or acquired by a job."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    artifact_id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    artifact_type: ArtifactType
    relative_path: str
    mime_type: str = Field(min_length=1)
    status: ArtifactStatus = ArtifactStatus.PENDING
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    duration_seconds: float | None = Field(default=None, gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    provider: str | None = None
    model_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_relative_path(value)
