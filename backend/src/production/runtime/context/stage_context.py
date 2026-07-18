"""Immutable execution context passed to production stage handlers."""

from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.domain.path_rules import validate_relative_path

_SENSITIVE_KEY_PARTS = ("api_key", "credential", "password", "secret", "token")


class StageContext(ContractModel):
    """Versioned data context; deliberately contains no executable services."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    job_id: UUID
    command_id: UUID
    stage: ProductionStage
    attempt_number: int = Field(ge=1)
    job_configuration: dict[str, Any] = Field(default_factory=dict)
    input_artifact_ids: tuple[UUID, ...] = ()
    workspace_relative_path: str
    correlation_id: UUID
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("workspace_relative_path")
    @classmethod
    def validate_workspace_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("workspace_relative_path must use POSIX separators")
        return normalized

    @field_validator("job_configuration", "metadata")
    @classmethod
    def reject_sensitive_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        def inspect(item: Any, *, path: str) -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    normalized = str(key).lower()
                    if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                        raise ValueError(f"sensitive key is not allowed in StageContext: {path}{key}")
                    inspect(child, path=f"{path}{key}.")
            elif isinstance(item, (list, tuple)):
                for index, child in enumerate(item):
                    inspect(child, path=f"{path}{index}.")

        inspect(value, path="")
        return value

    @model_validator(mode="after")
    def validate_artifact_ids(self) -> "StageContext":
        if len(self.input_artifact_ids) != len(set(self.input_artifact_ids)):
            raise ValueError("input_artifact_ids must be unique")
        return self
