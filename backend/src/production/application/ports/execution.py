"""Execution and decision-persistence ports owned by the application layer."""

from collections.abc import Callable, Collection
from typing import ParamSpec, Protocol, TypeVar

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.orchestration import OrchestrationDecision
from backend.src.production.application.results import StageResult
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.production_job import ProductionJob

P = ParamSpec("P")
R = TypeVar("R")


class BlockingExecutor(Protocol):
    """Run a synchronous application dependency without prescribing an adapter."""

    async def run(
        self,
        operation: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R: ...


class DecisionPersister(Protocol):
    """Persist one orchestration decision atomically through an injected adapter."""

    async def persist_decision(
        self,
        *,
        previous_job: ProductionJob,
        decision: OrchestrationDecision,
        processed_command: StageCommand | None = None,
        processed_result: StageResult | None = None,
        artifacts: Collection[Artifact] = (),
    ) -> object: ...
