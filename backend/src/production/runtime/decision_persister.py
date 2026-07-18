"""Runtime adapters for atomic decision persistence isolation."""

import asyncio
from collections.abc import Collection
from typing import Protocol

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.orchestration import OrchestrationDecision
from backend.src.production.application.results import StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.production_job import ProductionJob
from backend.src.production.infrastructure.persistence.transactions import (
    OrchestrationDecisionStore,
    PersistedDecision,
)


class RuntimeDecisionPersister(Protocol):
    async def persist_decision(
        self,
        *,
        previous_job: ProductionJob,
        decision: OrchestrationDecision,
        processed_command: StageCommand | None = None,
        processed_result: StageResult | None = None,
        artifacts: Collection[Artifact] = (),
    ) -> PersistedDecision: ...


class ImmediateRuntimeDecisionPersister:
    """Deterministic adapter for tests and explicitly synchronous runtimes."""

    def __init__(self, store: OrchestrationDecisionStore) -> None:
        self._store = store

    async def persist_decision(
        self,
        *,
        previous_job: ProductionJob,
        decision: OrchestrationDecision,
        processed_command: StageCommand | None = None,
        processed_result: StageResult | None = None,
        artifacts: Collection[Artifact] = (),
    ) -> PersistedDecision:
        return await self._store.persist_decision(
            previous_job=previous_job,
            decision=decision,
            processed_command=processed_command,
            processed_result=processed_result,
            artifacts=artifacts,
        )


class ThreadedRuntimeDecisionPersister:
    """Run the session-owning decision transaction in asyncio's default pool."""

    def __init__(self, store: OrchestrationDecisionStore) -> None:
        self._store = store

    async def persist_decision(
        self,
        *,
        previous_job: ProductionJob,
        decision: OrchestrationDecision,
        processed_command: StageCommand | None = None,
        processed_result: StageResult | None = None,
        artifacts: Collection[Artifact] = (),
    ) -> PersistedDecision:
        async def persist() -> PersistedDecision:
            return await self._store.persist_decision(
                previous_job=previous_job,
                decision=decision,
                processed_command=processed_command,
                processed_result=processed_result,
                artifacts=artifacts,
            )

        return await asyncio.to_thread(lambda: asyncio.run(persist()))
