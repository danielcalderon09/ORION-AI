from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QApplication

from backend.src.desktop.backend_client import DesktopJobSummary
from backend.src.desktop.main_window import OrionMainWindow
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.local_mvp import (
    LocalMvpFailure,
    LocalMvpOutput,
    LocalMvpProgress,
    LocalMvpReport,
    LocalMvpRequest,
)

JOB_ID = UUID("91000000-0000-4000-8000-000000000001")
RENDER_ID = UUID("91000000-0000-4000-8000-000000000002")
VALIDATION_ID = UUID("91000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 8, 1, 10, 30, tzinfo=UTC)


class FakeDesktopBackend:
    def __init__(
        self,
        *,
        jobs: tuple[DesktopJobSummary, ...] = (),
        report: LocalMvpReport | None = None,
        progress: tuple[LocalMvpProgress, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self.jobs = jobs
        self.report = report
        self.progress = progress
        self.error = error
        self.requests: list[LocalMvpRequest] = []

    async def list_jobs(self, *, limit: int = 25) -> tuple[DesktopJobSummary, ...]:
        assert limit == 25
        return self.jobs

    async def run_pipeline(
        self,
        request: LocalMvpRequest,
        *,
        progress_callback: Callable[[LocalMvpProgress], None],
        retry_failed: bool = False,
    ) -> LocalMvpReport:
        del retry_failed
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        for item in self.progress:
            progress_callback(item)
        assert self.report is not None
        return self.report


def _job(
    *,
    status: ProductionJobStatus = ProductionJobStatus.RUNNING,
    stage: ProductionStage = ProductionStage.GENERATING_NARRATION,
) -> DesktopJobSummary:
    return DesktopJobSummary(
        job_id=JOB_ID,
        title="Curiosidades de Marte",
        prompt="Explica tres curiosidades sobre Marte.",
        status=status,
        current_stage=stage,
        created_at=NOW,
        duration_seconds=30,
    )


def _output(tmp_path: Path) -> LocalMvpOutput:
    media = tmp_path / "orion-result.mp4"
    media.write_bytes(b"valid-local-video")
    return LocalMvpOutput(
        render_artifact_id=RENDER_ID,
        validation_artifact_id=VALIDATION_ID,
        workspace_relative_path=f"production/{JOB_ID}/output/orion-result.mp4",
        local_absolute_path=str(media),
        size_bytes=17,
        sha256="a" * 64,
        duration_ms=15_000,
        width=360,
        height=640,
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        video_codec="h264",
        audio_codec="aac",
    )


def _success_report(tmp_path: Path) -> LocalMvpReport:
    return LocalMvpReport(
        job_id=JOB_ID,
        status=ProductionJobStatus.COMPLETED,
        final_stage=ProductionStage.COMPLETED,
        success=True,
        resumed=False,
        stage_iterations=13,
        stages_succeeded=12,
        elapsed_seconds=2.5,
        output=_output(tmp_path),
    )


def _failed_report() -> LocalMvpReport:
    return LocalMvpReport(
        job_id=JOB_ID,
        status=ProductionJobStatus.FAILED,
        final_stage=ProductionStage.SCRIPTING,
        success=False,
        resumed=False,
        stage_iterations=2,
        stages_succeeded=1,
        elapsed_seconds=0.5,
        failure=LocalMvpFailure(
            error_code="controlled_failure",
            retryable=False,
            most_recent_attempt=1,
            completed_artifact_count=1,
            recommended_action="Revisa la entrada y vuelve a intentarlo.",
        ),
    )


def _progress(stage: ProductionStage, outcome: str, percent: float) -> LocalMvpProgress:
    return LocalMvpProgress(
        stage=stage,
        attempt_number=1,
        outcome=outcome,
        progress_percent=percent,
        artifacts_emitted=1 if outcome == "succeeded" else 0,
    )


def _wait_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout: float = 3,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Qt condition was not reached before timeout")


def _close(window: OrionMainWindow, application: QApplication) -> None:
    _wait_until(
        application,
        lambda: window._jobs_thread is None and window._pipeline_thread is None,  # noqa: SLF001
    )
    window.close()
    application.processEvents()


def test_window_creation_has_required_controls(
    qt_application: QApplication,
    qt_preferences: QSettings,
) -> None:
    window = OrionMainWindow(
        backend=FakeDesktopBackend(),
        preferences=qt_preferences,
        auto_load_jobs=False,
    )
    assert window.windowTitle() == "ORION AI"
    assert [window.duration_combo.itemText(index) for index in range(3)] == [
        "15 segundos",
        "30 segundos",
        "60 segundos",
    ]
    assert [window.format_combo.itemText(index) for index in range(3)] == [
        "9:16",
        "16:9",
        "1:1",
    ]
    assert window.generate_button.text() == "Generar Video"
    assert LocalMvpRequest(prompt="Marte", target_duration_seconds=60)
    _close(window, qt_application)


def test_jobs_panel_loads_real_backend_summaries(
    qt_application: QApplication,
    qt_preferences: QSettings,
) -> None:
    window = OrionMainWindow(
        backend=FakeDesktopBackend(jobs=(_job(),)),
        preferences=qt_preferences,
        auto_load_jobs=False,
    )
    window.refresh_jobs()
    _wait_until(qt_application, lambda: window.jobs_tree.topLevelItemCount() == 1)
    item = window.jobs_tree.topLevelItem(0)
    assert item.text(0) == "Curiosidades de Marte"
    assert item.text(1) == "En curso"
    assert item.text(3) == "30 s"
    item.setSelected(True)
    qt_application.processEvents()
    assert window.resume_button.isEnabled()
    _close(window, qt_application)


def test_progress_updates_from_backend_stage_events(
    qt_application: QApplication,
    qt_preferences: QSettings,
) -> None:
    backend = FakeDesktopBackend(
        report=_failed_report(),
        progress=(
            _progress(ProductionStage.PLANNING, "succeeded", 100),
            _progress(ProductionStage.SCRIPTING, "succeeded", 100),
        ),
    )
    window = OrionMainWindow(
        backend=backend,
        preferences=qt_preferences,
        auto_load_jobs=False,
    )
    window.prompt_edit.setPlainText("Explica Marte")
    window.start_generation()
    _wait_until(qt_application, lambda: window.generate_button.isEnabled())
    assert window._stage_states[ProductionStage.PLANNING].text() == "Completado"  # noqa: SLF001
    assert window._stage_states[ProductionStage.SCRIPTING].text() == "Completado"  # noqa: SLF001
    assert window._stage_states[ProductionStage.SCENE_PLANNING].text() == "Pendiente"  # noqa: SLF001
    assert window.overall_progress.value() > 0
    _close(window, qt_application)


def test_success_displays_validated_video_result(
    tmp_path: Path,
    qt_application: QApplication,
    qt_preferences: QSettings,
) -> None:
    backend = FakeDesktopBackend(
        report=_success_report(tmp_path),
        progress=(_progress(ProductionStage.VALIDATING_RENDER, "succeeded", 100),),
    )
    window = OrionMainWindow(
        backend=backend,
        preferences=qt_preferences,
        auto_load_jobs=False,
    )
    window.prompt_edit.setPlainText("Explica Marte")
    window.start_generation()
    _wait_until(qt_application, lambda: not window.result_content.isHidden())
    assert window.result_path.text().endswith("orion-result.mp4")
    assert window.result_duration.text() == "15.000 s"
    assert window.result_resolution.text() == "360 × 640"
    assert window.result_fps.text() == "24.000"
    assert window.result_codec.text() == "H264 / AAC"
    assert window.result_sha256.text() == "a" * 64
    _close(window, qt_application)


def test_backend_error_is_friendly_and_has_no_traceback(
    qt_application: QApplication,
    qt_preferences: QSettings,
) -> None:
    window = OrionMainWindow(
        backend=FakeDesktopBackend(error=RuntimeError("Fallo local controlado")),
        preferences=qt_preferences,
        auto_load_jobs=False,
    )
    window.prompt_edit.setPlainText("Explica Marte")
    window.start_generation()
    _wait_until(qt_application, lambda: not window.error_banner.isHidden())
    assert window.error_banner.text() == "Fallo local controlado"
    assert "Traceback" not in window.error_banner.text()
    _close(window, qt_application)


def test_visual_preferences_are_restored(
    qt_application: QApplication,
    qt_preferences: QSettings,
) -> None:
    first = OrionMainWindow(
        backend=FakeDesktopBackend(),
        preferences=qt_preferences,
        auto_load_jobs=False,
    )
    first.duration_combo.setCurrentText("60 segundos")
    first.format_combo.setCurrentText("1:1")
    first.resize(1180, 760)
    _close(first, qt_application)

    second = OrionMainWindow(
        backend=FakeDesktopBackend(),
        preferences=qt_preferences,
        auto_load_jobs=False,
    )
    assert second.duration_combo.currentText() == "60 segundos"
    assert second.format_combo.currentText() == "1:1"
    geometry = qt_preferences.value("window/geometry")
    assert isinstance(geometry, QByteArray) and not geometry.isEmpty()
    assert second.size().width() >= 1050
    assert second.size().height() == 760
    _close(second, qt_application)
