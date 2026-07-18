"""Explicit app-scoped Production composition."""

from backend.src.production.composition.container import (
    ProductionContainer,
    build_production_container,
)
from backend.src.production.composition.lifecycle import production_lifespan

__all__ = ["ProductionContainer", "build_production_container", "production_lifespan"]
