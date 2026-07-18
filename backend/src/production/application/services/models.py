"""Read models shared by Production application services and HTTP schemas."""

from datetime import datetime
from typing import Any

from pydantic import Field

from backend.src.production.application.events import ProductionEventUnion
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.production_job import ProductionJob


class ProductionJobView(ContractModel):
    job: ProductionJob
    row_version: int = Field(ge=1)
    completed_at: datetime | None = None


class ProductionJobPage(ContractModel):
    items: tuple[ProductionJobView, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ProductionArtifactView(ContractModel):
    artifact: Artifact
    created_at: datetime
    updated_at: datetime


class ProductionEventPage(ContractModel):
    items: tuple[ProductionEventUnion, ...]


class ProductionArtifactPage(ContractModel):
    items: tuple[ProductionArtifactView, ...]


class CreateProductionJobCommand(ContractModel):
    prompt: str = Field(min_length=1, max_length=10_000)
    configuration: dict[str, Any] = Field(default_factory=dict)
    generate_clips_after_render: bool = False
    client_request_id: str | None = Field(default=None, min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductionOperationResult(ContractModel):
    job: ProductionJobView
    operation: str
    idempotent: bool = False
