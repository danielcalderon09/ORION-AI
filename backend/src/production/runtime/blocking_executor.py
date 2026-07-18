"""Isolation policy for short synchronous database operations."""

import asyncio
from collections.abc import Callable
from typing import ParamSpec, Protocol, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


class RuntimeBlockingExecutor(Protocol):
    async def run(
        self,
        operation: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R: ...


class ThreadedRuntimeBlockingExecutor:
    """Run a session-owning synchronous operation in asyncio's default pool."""

    async def run(
        self,
        operation: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        return await asyncio.to_thread(operation, *args, **kwargs)


class ImmediateRuntimeBlockingExecutor:
    """Deterministic test implementation that stays on the calling thread."""

    async def run(
        self,
        operation: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        return operation(*args, **kwargs)
