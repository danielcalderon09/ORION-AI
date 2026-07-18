"""FastAPI dependencies for the app-scoped Production container."""

from fastapi import Request

from backend.src.production.application.services.exceptions import (
    ProductionRuntimeUnavailableError,
)
from backend.src.production.composition.container import ProductionContainer


def get_production_container(request: Request) -> ProductionContainer:
    container = getattr(request.app.state, "production_container", None)
    if not isinstance(container, ProductionContainer):
        raise ProductionRuntimeUnavailableError("production runtime is unavailable")
    return container
