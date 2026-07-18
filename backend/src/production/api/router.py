"""Thin HTTP controller for internal Production Job use cases."""

from collections.abc import Awaitable
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from backend.src.production.api.dependencies import get_production_container
from backend.src.production.api.error_mapping import raise_production_http_error
from backend.src.production.api.schemas import (
    CreateProductionJobRequest,
    ProductionArtifactListResponse,
    ProductionArtifactResponse,
    ProductionEventListResponse,
    ProductionEventResponse,
    ProductionJobListResponse,
    ProductionJobResponse,
    ProductionOperationResponse,
)
from backend.src.production.application.services.models import CreateProductionJobCommand
from backend.src.production.composition.container import ProductionContainer
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage

router = APIRouter(prefix="/production/jobs", tags=["production"])
T = TypeVar("T")
ContainerDependency = Annotated[ProductionContainer, Depends(get_production_container)]


async def _call(operation: Awaitable[T]) -> T:
    try:
        return await operation
    except Exception as exc:
        raise_production_http_error(exc)


@router.post("", response_model=ProductionJobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    request: CreateProductionJobRequest,
    container: ContainerDependency,
) -> ProductionJobResponse:
    view = await _call(container.create_job.execute(CreateProductionJobCommand(**request.model_dump())))
    return ProductionJobResponse.from_view(view)


@router.get("", response_model=ProductionJobListResponse)
async def list_jobs(
    container: ContainerDependency,
    job_status: Annotated[
        ProductionJobStatus | None, Query(alias="status")
    ] = None,
    stage: ProductionStage | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductionJobListResponse:
    page = await _call(
        container.list_jobs.execute(status=job_status, stage=stage, limit=limit, offset=offset)
    )
    return ProductionJobListResponse(
        items=tuple(ProductionJobResponse.from_view(item) for item in page.items),
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{job_id}", response_model=ProductionJobResponse)
async def get_job(
    job_id: UUID,
    container: ContainerDependency,
) -> ProductionJobResponse:
    return ProductionJobResponse.from_view(await _call(container.get_job.execute(job_id)))


@router.post("/{job_id}/cancel", response_model=ProductionOperationResponse)
async def cancel_job(
    job_id: UUID,
    container: ContainerDependency,
) -> ProductionOperationResponse:
    result = await _call(container.cancel_job.execute(job_id))
    return ProductionOperationResponse(
        operation=result.operation,
        idempotent=result.idempotent,
        job=ProductionJobResponse.from_view(result.job),
    )


@router.post("/{job_id}/retry", response_model=ProductionOperationResponse)
async def retry_job(
    job_id: UUID,
    container: ContainerDependency,
) -> ProductionOperationResponse:
    result = await _call(container.retry_job.execute(job_id))
    return ProductionOperationResponse(
        operation=result.operation,
        idempotent=result.idempotent,
        job=ProductionJobResponse.from_view(result.job),
    )


@router.get("/{job_id}/events", response_model=ProductionEventListResponse)
async def list_events(
    job_id: UUID,
    container: ContainerDependency,
) -> ProductionEventListResponse:
    page = await _call(container.list_events.execute(job_id))
    return ProductionEventListResponse(
        items=tuple(ProductionEventResponse.from_event(item) for item in page.items)
    )


@router.get("/{job_id}/artifacts", response_model=ProductionArtifactListResponse)
async def list_artifacts(
    job_id: UUID,
    container: ContainerDependency,
) -> ProductionArtifactListResponse:
    page = await _call(container.list_artifacts.execute(job_id))
    return ProductionArtifactListResponse(
        items=tuple(ProductionArtifactResponse.from_view(item) for item in page.items)
    )
