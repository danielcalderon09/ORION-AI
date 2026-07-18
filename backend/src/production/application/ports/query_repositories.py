"""Read-only ports supporting the internal Production API."""

from typing import Protocol
from uuid import UUID

from backend.src.production.application.events import ProductionEventUnion
from backend.src.production.application.services.models import (
    ProductionArtifactPage,
    ProductionJobPage,
    ProductionJobView,
)
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage


class ProductionJobQueryRepository(Protocol):
    def get(self, job_id: UUID) -> ProductionJobView | None: ...
    def get_by_client_request_id(self, request_id: str) -> ProductionJobView | None: ...
    def list(
        self,
        *,
        status: ProductionJobStatus | None,
        stage: ProductionStage | None,
        limit: int,
        offset: int,
    ) -> ProductionJobPage: ...


class ProductionEventQueryRepository(Protocol):
    def list_for_job(self, job_id: UUID) -> tuple[ProductionEventUnion, ...]: ...
    def latest_for_job(self, job_id: UUID) -> ProductionEventUnion | None: ...
    def next_sequence(self, job_id: UUID) -> int: ...


class ProductionArtifactQueryRepository(Protocol):
    def list_for_job(self, job_id: UUID) -> ProductionArtifactPage: ...
