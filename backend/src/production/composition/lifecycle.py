"""Controlled startup and shutdown of the simulated Production worker."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.services.exceptions import (
    ProductionRuntimeUnavailableError,
)
from backend.src.production.composition.container import (
    ProductionContainer,
    build_production_container,
)
from backend.src.production.composition.schema import ensure_production_schema


async def start_production_runtime(
    app: FastAPI,
    settings: Settings,
    *,
    container: ProductionContainer | None = None,
) -> None:
    existing = getattr(app.state, "production_container", None)
    if existing is not None:
        raise ProductionRuntimeUnavailableError("Production runtime already started")
    built = container or build_production_container(settings)
    try:
        await ensure_production_schema(settings, built.engine)
        await built.recovery.recover()
        stop_event = asyncio.Event()
        task = None
        if settings.ORION_PRODUCTION_WORKER_ENABLED:
            task = asyncio.create_task(
                built.worker.run_forever(
                    stop_event=stop_event,
                    poll_interval_seconds=settings.ORION_PRODUCTION_POLL_INTERVAL_SECONDS,
                ),
                name="orion-production-worker",
            )
        app.state.production_container = built
        app.state.production_stop_event = stop_event
        app.state.production_worker_task = task
    except BaseException:
        built.shutdown()
        raise


async def stop_production_runtime(app: FastAPI, settings: Settings) -> None:
    container = getattr(app.state, "production_container", None)
    if container is None:
        return
    stop_event = getattr(app.state, "production_stop_event", None)
    task = getattr(app.state, "production_worker_task", None)
    if stop_event is not None:
        stop_event.set()
    if task is not None:
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=settings.ORION_PRODUCTION_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    container.shutdown()
    for name in (
        "production_worker_task",
        "production_stop_event",
        "production_container",
    ):
        if hasattr(app.state, name):
            delattr(app.state, name)


@asynccontextmanager
async def production_lifespan(
    app: FastAPI,
    settings: Settings,
) -> AsyncIterator[None]:
    await start_production_runtime(app, settings)
    try:
        yield
    finally:
        await stop_production_runtime(app, settings)
