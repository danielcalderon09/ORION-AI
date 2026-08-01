"""Qt worker threads that keep Production work off the GUI thread."""

from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError
from PySide6.QtCore import QThread, Signal

from backend.src.desktop.backend_client import DesktopBackend
from backend.src.production.application.services.exceptions import ProductionApplicationError
from backend.src.production.local_mvp import LocalMvpProgress, LocalMvpRequest

logger = logging.getLogger(__name__)


class JobsLoaderThread(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, backend: DesktopBackend, *, parent: object | None = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._backend = backend

    def run(self) -> None:
        try:
            jobs = asyncio.run(self._backend.list_jobs())
        except Exception as exc:  # Qt thread boundary converts to a safe UI signal.
            logger.exception("desktop job loading failed")
            self.failed.emit(_friendly_error(exc))
            return
        self.loaded.emit(jobs)


class PipelineThread(QThread):
    progressed = Signal(object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        backend: DesktopBackend,
        request: LocalMvpRequest,
        *,
        retry_failed: bool = False,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._backend = backend
        self._request = request
        self._retry_failed = retry_failed

    def run(self) -> None:
        try:
            report = asyncio.run(
                self._backend.run_pipeline(
                    self._request,
                    progress_callback=self._emit_progress,
                    retry_failed=self._retry_failed,
                )
            )
        except Exception as exc:  # Qt thread boundary converts to a safe UI signal.
            logger.exception("desktop pipeline execution failed")
            self.failed.emit(_friendly_error(exc))
            return
        self.completed.emit(report)

    def _emit_progress(self, progress: LocalMvpProgress) -> None:
        self.progressed.emit(progress)


def _friendly_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())[:300]
    lowered = message.casefold()
    if "ffmpeg" in lowered or "ffprobe" in lowered:
        return "FFmpeg y FFprobe deben estar instalados y disponibles localmente."
    if "production schema" in lowered or "alembic" in lowered:
        return "No fue posible preparar la base local de trabajos de ORION."
    if isinstance(exc, ValidationError):
        return "Revisa el prompt, la duración y el formato seleccionados."
    if isinstance(exc, ProductionApplicationError):
        return message or "El backend rechazó la operación solicitada."
    return message or "No fue posible completar la operación local."


__all__ = ["JobsLoaderThread", "PipelineThread"]
