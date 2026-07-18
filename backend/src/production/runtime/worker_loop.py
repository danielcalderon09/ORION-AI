"""Reusable loop policy around a single worker cycle."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

from backend.src.production.runtime.runtime_models import WorkerRunResult


class ProductionWorkerLoopError(RuntimeError):
    """Raised when configured loop safety limits are exceeded."""


class ProductionWorkerLoop:
    def __init__(self, run_once: Callable[[], Awaitable[WorkerRunResult]]) -> None:
        self._run_once = run_once

    async def run_until_idle(self, *, max_cycles: int = 100) -> tuple[WorkerRunResult, ...]:
        if max_cycles < 1:
            raise ValueError("max_cycles must be positive")
        results: list[WorkerRunResult] = []
        for _ in range(max_cycles):
            result = await self._run_once()
            results.append(result)
            if not result.processed:
                return tuple(results)
        raise ProductionWorkerLoopError("worker did not become idle within max_cycles")

    async def run_forever(
        self,
        *,
        stop_event: asyncio.Event,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        while not stop_event.is_set():
            result = await self._run_once()
            if not result.processed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=poll_interval_seconds,
                    )
