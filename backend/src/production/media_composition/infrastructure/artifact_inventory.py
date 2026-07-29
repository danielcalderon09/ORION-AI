"""Adapter exposing the durable artifact registry to composition."""

import asyncio
from typing import Protocol
from uuid import UUID

from backend.src.production.application.services.models import ProductionArtifactPage
from backend.src.production.domain.artifact import Artifact


class ProductionArtifactQuery(Protocol):
    def list_for_job(self, job_id: UUID) -> ProductionArtifactPage: ...


class SQLAlchemyMediaCompositionArtifactInventory:
    def __init__(self, query: ProductionArtifactQuery) -> None:
        self._query = query

    async def list_for_job(self, job_id: UUID) -> tuple[Artifact, ...]:
        page = await asyncio.to_thread(self._query.list_for_job, job_id)
        return tuple(item.artifact for item in page.items)
