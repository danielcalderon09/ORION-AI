"""Safe translation of known application failures into HTTP errors."""

from typing import NoReturn

from fastapi import HTTPException

from backend.src.production.application.services.exceptions import (
    ProductionJobConflictError,
    ProductionJobNotFoundError,
    ProductionJobStateError,
    ProductionRequestIdConflictError,
    ProductionRuntimeUnavailableError,
    ProductionValidationError,
)
from backend.src.production.infrastructure.persistence.exceptions import (
    ProductionConcurrencyError,
)


def raise_production_http_error(error: Exception) -> NoReturn:
    status = 500
    code = "production_internal_error"
    message = "Production operation failed"
    if isinstance(error, ProductionJobNotFoundError):
        status, code, message = 404, "production_job_not_found", str(error)
    elif isinstance(
        error,
        (
            ProductionJobConflictError,
            ProductionJobStateError,
            ProductionRequestIdConflictError,
            ProductionConcurrencyError,
        ),
    ):
        status, code, message = 409, "production_conflict", str(error)
    elif isinstance(error, ProductionValidationError):
        status, code, message = 400, "production_validation_error", str(error)
    elif isinstance(error, ProductionRuntimeUnavailableError):
        status, code, message = 503, "production_runtime_unavailable", str(error)
    raise HTTPException(status_code=status, detail={"code": code, "message": message}) from error
