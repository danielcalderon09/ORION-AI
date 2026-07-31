"""Durable final acceptance of an already-rendered local video."""

from backend.src.production.render_validation.handler import FinalRenderValidationHandler
from backend.src.production.render_validation.models import FinalRenderValidationManifest

__all__ = ["FinalRenderValidationHandler", "FinalRenderValidationManifest"]
