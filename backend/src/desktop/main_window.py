"""Main native ORION desktop window."""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal, cast

from PySide6.QtCore import QByteArray, QSettings, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backend.src.desktop.backend_client import (
    DesktopBackend,
    DesktopJobSummary,
    ProductionDesktopBackend,
)
from backend.src.desktop.styles import ORION_DARK_STYLESHEET
from backend.src.desktop.workers import JobsLoaderThread, PipelineThread
from backend.src.production.domain.enums import ProductionJobStatus, ProductionStage
from backend.src.production.local_mvp import (
    LocalMvpOutput,
    LocalMvpProgress,
    LocalMvpReport,
    LocalMvpRequest,
)

STAGES: Final[tuple[tuple[ProductionStage, str], ...]] = (
    (ProductionStage.PLANNING, "Planning"),
    (ProductionStage.SCRIPTING, "Script"),
    (ProductionStage.SCENE_PLANNING, "Scenes"),
    (ProductionStage.VISUAL_ASSET_PLANNING, "Visual Planning"),
    (ProductionStage.ACQUIRING_ASSETS, "Assets"),
    (ProductionStage.GENERATING_VIDEO_CLIPS, "Video Clips"),
    (ProductionStage.GENERATING_NARRATION, "Narration"),
    (ProductionStage.PREPARING_MUSIC, "Music"),
    (ProductionStage.GENERATING_SUBTITLES, "Subtitles"),
    (ProductionStage.BUILDING_TIMELINE, "Timeline"),
    (ProductionStage.RENDERING_LONG_FORM, "Rendering"),
    (ProductionStage.VALIDATING_RENDER, "Validation"),
)
STAGE_INDEX: Final[dict[ProductionStage, int]] = {
    stage: index for index, (stage, _) in enumerate(STAGES)
}


class OrionMainWindow(QMainWindow):
    def __init__(
        self,
        *,
        backend: DesktopBackend | None = None,
        preferences: QSettings | None = None,
        auto_load_jobs: bool = True,
    ) -> None:
        super().__init__()
        self._backend = backend or ProductionDesktopBackend()
        self._preferences = preferences or QSettings("ORION", "ORION AI")
        self._jobs_thread: JobsLoaderThread | None = None
        self._pipeline_thread: PipelineThread | None = None
        self._current_output: LocalMvpOutput | None = None
        self._last_open_folder = str(self._preferences.value("paths/last_open_folder", ""))
        self._stage_states: dict[ProductionStage, QLabel] = {}

        self.setWindowTitle("ORION AI")
        self.setMinimumSize(1050, 700)
        self.resize(1320, 820)
        self.setStyleSheet(ORION_DARK_STYLESHEET)
        self._build_ui()
        self._restore_preferences()
        self._reset_progress()
        if auto_load_jobs:
            self.refresh_jobs()

    def _build_ui(self) -> None:
        root = QWidget(self)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(26, 24, 26, 24)
        root_layout.setSpacing(22)
        root_layout.addWidget(self._build_jobs_panel(), 0)

        scroll = QScrollArea(root)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(18)
        content_layout.addWidget(self._build_header())
        content_layout.addWidget(self._build_creation_card())
        content_layout.addWidget(self._build_progress_card())
        content_layout.addWidget(self._build_result_card())
        content_layout.addStretch(1)
        scroll.setWidget(content)
        root_layout.addWidget(scroll, 1)
        self.setCentralWidget(root)

    def _build_header(self) -> QWidget:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(2, 0, 0, 2)
        layout.setSpacing(3)
        title = QLabel("ORION AI", widget)
        title.setObjectName("appTitle")
        subtitle = QLabel("Producción local de video · MVP", widget)
        subtitle.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        return widget

    def _build_jobs_panel(self) -> QFrame:
        panel = QFrame(self)
        panel.setObjectName("panel")
        panel.setFixedWidth(430)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel("Últimos trabajos", panel)
        title.setObjectName("sectionTitle")
        self.refresh_button = QPushButton("Actualizar", panel)
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.clicked.connect(self.refresh_jobs)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)

        self.jobs_tree = QTreeWidget(panel)
        self.jobs_tree.setObjectName("jobsTree")
        self.jobs_tree.setColumnCount(4)
        self.jobs_tree.setHeaderLabels(("Trabajo", "Estado", "Fecha", "Duración"))
        self.jobs_tree.setAlternatingRowColors(True)
        self.jobs_tree.setRootIsDecorated(False)
        self.jobs_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.jobs_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.jobs_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.jobs_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.jobs_tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.jobs_tree.itemSelectionChanged.connect(self._job_selection_changed)
        layout.addWidget(self.jobs_tree, 1)

        self.jobs_message = QLabel("Cargando trabajos…", panel)
        self.jobs_message.setObjectName("muted")
        self.jobs_message.setWordWrap(True)
        layout.addWidget(self.jobs_message)

        self.resume_button = QPushButton("Reanudar", panel)
        self.resume_button.setObjectName("resumeButton")
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self.resume_selected_job)
        layout.addWidget(self.resume_button)
        return panel

    def _build_creation_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)
        title = QLabel("Nueva producción", card)
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        prompt_label = QLabel("Idea del video", card)
        prompt_label.setObjectName("muted")
        layout.addWidget(prompt_label)
        self.prompt_edit = QTextEdit(card)
        self.prompt_edit.setObjectName("promptEdit")
        self.prompt_edit.setPlaceholderText(
            "Ejemplo: Explica en un video corto tres curiosidades sobre Marte."
        )
        self.prompt_edit.setMinimumHeight(120)
        layout.addWidget(self.prompt_edit)

        options = QHBoxLayout()
        options.setSpacing(12)
        duration_box = QVBoxLayout()
        duration_label = QLabel("Duración", card)
        duration_label.setObjectName("muted")
        self.duration_combo = QComboBox(card)
        self.duration_combo.setObjectName("durationCombo")
        self.duration_combo.addItems(("15 segundos", "30 segundos", "60 segundos"))
        duration_box.addWidget(duration_label)
        duration_box.addWidget(self.duration_combo)
        options.addLayout(duration_box, 1)

        format_box = QVBoxLayout()
        format_label = QLabel("Formato", card)
        format_label.setObjectName("muted")
        self.format_combo = QComboBox(card)
        self.format_combo.setObjectName("formatCombo")
        self.format_combo.addItems(("9:16", "16:9", "1:1"))
        format_box.addWidget(format_label)
        format_box.addWidget(self.format_combo)
        options.addLayout(format_box, 1)
        layout.addLayout(options)

        self.error_banner = QLabel("", card)
        self.error_banner.setObjectName("errorBanner")
        self.error_banner.setWordWrap(True)
        self.error_banner.hide()
        layout.addWidget(self.error_banner)

        self.generate_button = QPushButton("Generar Video", card)
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.setMinimumHeight(44)
        self.generate_button.clicked.connect(self.start_generation)
        layout.addWidget(self.generate_button)
        return card

    def _build_progress_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(11)
        top = QHBoxLayout()
        title = QLabel("Progreso", card)
        title.setObjectName("sectionTitle")
        self.progress_summary = QLabel("Listo para comenzar", card)
        self.progress_summary.setObjectName("muted")
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.progress_summary)
        layout.addLayout(top)

        self.overall_progress = QProgressBar(card)
        self.overall_progress.setObjectName("overallProgress")
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setTextVisible(False)
        layout.addWidget(self.overall_progress)

        stages = QGridLayout()
        stages.setHorizontalSpacing(28)
        stages.setVerticalSpacing(9)
        for index, (stage, display_name) in enumerate(STAGES):
            row, column = divmod(index, 2)
            item = QHBoxLayout()
            name = QLabel(display_name, card)
            state = QLabel("Pendiente", card)
            state.setObjectName(f"stage_{stage.value}")
            state.setProperty("stageState", "pending")
            state.setAlignment(Qt.AlignmentFlag.AlignRight)
            item.addWidget(name)
            item.addStretch(1)
            item.addWidget(state)
            stages.addLayout(item, row, column)
            self._stage_states[stage] = state
        layout.addLayout(stages)
        return card

    def _build_result_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(11)
        title = QLabel("Resultado", card)
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        self.result_empty = QLabel("El MP4 validado aparecerá aquí.", card)
        self.result_empty.setObjectName("muted")
        layout.addWidget(self.result_empty)

        self.result_content = QWidget(card)
        result_layout = QVBoxLayout(self.result_content)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(10)
        self.result_path = QLineEdit(self.result_content)
        self.result_path.setObjectName("resultPath")
        self.result_path.setReadOnly(True)
        result_layout.addWidget(self.result_path)

        metrics = QGridLayout()
        self.result_duration = self._metric(metrics, 0, 0, "Duración")
        self.result_resolution = self._metric(metrics, 0, 1, "Resolución")
        self.result_fps = self._metric(metrics, 1, 0, "FPS")
        self.result_codec = self._metric(metrics, 1, 1, "Codec")
        self.result_sha256 = self._metric(metrics, 2, 0, "SHA-256", column_span=2)
        result_layout.addLayout(metrics)

        buttons = QHBoxLayout()
        self.open_video_button = QPushButton("Abrir video", self.result_content)
        self.open_video_button.setObjectName("openVideoButton")
        self.open_video_button.clicked.connect(self.open_video)
        self.open_folder_button = QPushButton("Abrir carpeta", self.result_content)
        self.open_folder_button.setObjectName("openFolderButton")
        self.open_folder_button.clicked.connect(self.open_output_folder)
        buttons.addWidget(self.open_video_button)
        buttons.addWidget(self.open_folder_button)
        buttons.addStretch(1)
        result_layout.addLayout(buttons)
        self.result_content.hide()
        layout.addWidget(self.result_content)
        return card

    @staticmethod
    def _metric(
        layout: QGridLayout,
        row: int,
        column: int,
        label: str,
        *,
        column_span: int = 1,
    ) -> QLabel:
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        box = QVBoxLayout(container)
        box.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label, container)
        name.setObjectName("muted")
        value = QLabel("—", container)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setWordWrap(True)
        box.addWidget(name)
        box.addWidget(value)
        layout.addWidget(container, row, column, 1, column_span)
        return value

    def refresh_jobs(self) -> None:
        if self._jobs_thread is not None and self._jobs_thread.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.jobs_message.setText("Cargando trabajos…")
        thread = JobsLoaderThread(self._backend, parent=self)
        thread.loaded.connect(self._jobs_loaded)
        thread.failed.connect(self._jobs_failed)
        thread.finished.connect(self._jobs_thread_finished)
        self._jobs_thread = thread
        thread.start()

    def _jobs_loaded(self, jobs: object) -> None:
        if not isinstance(jobs, tuple):
            self._jobs_failed("El backend devolvió un listado de trabajos inválido.")
            return
        self.jobs_tree.clear()
        for summary in jobs:
            if not isinstance(summary, DesktopJobSummary):
                continue
            item = QTreeWidgetItem(
                (
                    summary.title,
                    _status_name(summary.status),
                    summary.created_at.astimezone().strftime("%d/%m %H:%M"),
                    f"{summary.duration_seconds} s" if summary.duration_seconds else "—",
                )
            )
            item.setData(0, Qt.ItemDataRole.UserRole, summary)
            item.setToolTip(0, summary.prompt)
            self.jobs_tree.addTopLevelItem(item)
        self.jobs_message.setText(
            "No hay trabajos todavía." if not jobs else f"{len(jobs)} trabajos recientes"
        )

    def _jobs_failed(self, message: str) -> None:
        self.jobs_message.setText(message)

    def _jobs_thread_finished(self) -> None:
        self.refresh_button.setEnabled(True)
        thread = self._jobs_thread
        self._jobs_thread = None
        if thread is not None:
            thread.deleteLater()

    def _job_selection_changed(self) -> None:
        summary = self._selected_job()
        enabled = summary is not None and (summary.can_resume or summary.has_result)
        self.resume_button.setEnabled(enabled and not self._pipeline_running())
        self.resume_button.setText(
            "Ver resultado" if summary is not None and summary.has_result else "Reanudar"
        )

    def _selected_job(self) -> DesktopJobSummary | None:
        selected = self.jobs_tree.selectedItems()
        if not selected:
            return None
        value = selected[0].data(0, Qt.ItemDataRole.UserRole)
        return value if isinstance(value, DesktopJobSummary) else None

    def start_generation(self) -> None:
        prompt = " ".join(self.prompt_edit.toPlainText().split())
        if not prompt:
            self._show_error("Escribe una idea para el video antes de comenzar.")
            return
        self._hide_error()
        self._reset_progress()
        request = LocalMvpRequest(
            prompt=prompt,
            target_duration_seconds=int(self.duration_combo.currentText().split()[0]),
            aspect_ratio=cast(
                Literal["9:16", "16:9", "1:1"],
                self.format_combo.currentText(),
            ),
        )
        self._start_pipeline(request, retry_failed=False)

    def resume_selected_job(self) -> None:
        summary = self._selected_job()
        if summary is None or not (summary.can_resume or summary.has_result):
            return
        self._hide_error()
        self._set_progress_from_job(summary)
        self._start_pipeline(
            LocalMvpRequest(resume_job_id=summary.job_id),
            retry_failed=summary.status
            in {ProductionJobStatus.FAILED, ProductionJobStatus.NEEDS_USER_ACTION},
        )

    def _start_pipeline(self, request: LocalMvpRequest, *, retry_failed: bool) -> None:
        if self._pipeline_running():
            return
        self._set_busy(True)
        thread = PipelineThread(
            self._backend,
            request,
            retry_failed=retry_failed,
            parent=self,
        )
        thread.progressed.connect(self._progress_changed)
        thread.completed.connect(self._pipeline_completed)
        thread.failed.connect(self._pipeline_failed)
        thread.finished.connect(self._pipeline_thread_finished)
        self._pipeline_thread = thread
        thread.start()

    def _progress_changed(self, value: object) -> None:
        if not isinstance(value, LocalMvpProgress):
            return
        index = STAGE_INDEX.get(value.stage)
        if index is None:
            return
        for previous, (stage, _) in enumerate(STAGES):
            if previous < index:
                self._set_stage_state(stage, "complete", "Completado")
        if value.outcome == "succeeded":
            self._set_stage_state(value.stage, "complete", "Completado")
        elif value.outcome in {"failed", "manual_intervention", "cancelled"}:
            self._set_stage_state(value.stage, "failed", "Error")
        else:
            self._set_stage_state(value.stage, "active", "En curso")
        overall = round(((index + value.progress_percent / 100) / len(STAGES)) * 100)
        self.overall_progress.setValue(max(self.overall_progress.value(), overall))
        self.progress_summary.setText(
            f"{STAGES[index][1]} · intento {value.attempt_number} · {overall}%"
        )

    def _pipeline_completed(self, value: object) -> None:
        if not isinstance(value, LocalMvpReport):
            self._pipeline_failed("El backend devolvió un resultado inválido.")
            return
        self._set_busy(False)
        if not value.success or value.output is None:
            message = (
                value.failure.recommended_action
                if value.failure is not None
                else "La producción se detuvo sin un MP4 validado."
            )
            self._show_error(message)
            self.progress_summary.setText(
                f"Detenido en {value.final_stage.value} · {value.status.value}"
            )
            return
        for stage, _ in STAGES:
            self._set_stage_state(stage, "complete", "Completado")
        self.overall_progress.setValue(100)
        self.progress_summary.setText("Video completado y validado")
        self._show_result(value.output)

    def _pipeline_failed(self, message: str) -> None:
        self._set_busy(False)
        self._show_error(message)
        self.progress_summary.setText("La producción se detuvo")

    def _pipeline_thread_finished(self) -> None:
        thread = self._pipeline_thread
        self._pipeline_thread = None
        if thread is not None:
            thread.deleteLater()
        self._set_busy(False)
        self.refresh_jobs()

    def _show_result(self, output: LocalMvpOutput) -> None:
        self._current_output = output
        self.result_empty.hide()
        self.result_content.show()
        self.result_path.setText(output.local_absolute_path)
        self.result_duration.setText(f"{output.duration_ms / 1000:.3f} s")
        self.result_resolution.setText(f"{output.width} × {output.height}")
        fps = output.frame_rate_numerator / output.frame_rate_denominator
        self.result_fps.setText(f"{fps:.3f}")
        self.result_codec.setText(
            f"{output.video_codec.upper()} / {(output.audio_codec or 'sin audio').upper()}"
        )
        self.result_sha256.setText(output.sha256)

    def open_video(self) -> None:
        path = self._current_output_path()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_output_folder(self) -> None:
        path = self._current_output_path()
        if path is None:
            return
        folder = path.parent
        self._last_open_folder = str(folder)
        self._preferences.setValue("paths/last_open_folder", self._last_open_folder)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _current_output_path(self) -> Path | None:
        if self._current_output is None:
            return None
        path = Path(self._current_output.local_absolute_path)
        if not path.is_file():
            QMessageBox.warning(
                self,
                "Archivo no disponible",
                "El archivo validado ya no existe en su ubicación local.",
            )
            return None
        return path

    def _set_progress_from_job(self, summary: DesktopJobSummary) -> None:
        self._reset_progress()
        if summary.status is ProductionJobStatus.COMPLETED:
            for stage, _ in STAGES:
                self._set_stage_state(stage, "complete", "Completado")
            self.overall_progress.setValue(100)
            self.progress_summary.setText("Cargando resultado validado…")
            return
        index = STAGE_INDEX.get(summary.current_stage)
        if index is None:
            return
        for position, (stage, _) in enumerate(STAGES):
            if position < index:
                self._set_stage_state(stage, "complete", "Completado")
        state = "failed" if summary.status is ProductionJobStatus.FAILED else "active"
        text = "Error" if state == "failed" else "En curso"
        self._set_stage_state(summary.current_stage, state, text)
        self.overall_progress.setValue(round((index / len(STAGES)) * 100))

    def _reset_progress(self) -> None:
        for stage, _ in STAGES:
            self._set_stage_state(stage, "pending", "Pendiente")
        self.overall_progress.setValue(0)
        self.progress_summary.setText("Listo para comenzar")

    def _set_stage_state(self, stage: ProductionStage, state: str, text: str) -> None:
        label = self._stage_states[stage]
        label.setText(text)
        label.setProperty("stageState", state)
        label.style().unpolish(label)
        label.style().polish(label)

    def _set_busy(self, busy: bool) -> None:
        self.generate_button.setEnabled(not busy)
        self.prompt_edit.setEnabled(not busy)
        self.duration_combo.setEnabled(not busy)
        self.format_combo.setEnabled(not busy)
        self._job_selection_changed()

    def _pipeline_running(self) -> bool:
        return self._pipeline_thread is not None and self._pipeline_thread.isRunning()

    def _show_error(self, message: str) -> None:
        self.error_banner.setText(message)
        self.error_banner.show()

    def _hide_error(self) -> None:
        self.error_banner.clear()
        self.error_banner.hide()

    def _restore_preferences(self) -> None:
        duration = str(self._preferences.value("generation/duration", "15"))
        duration_index = self.duration_combo.findText(f"{duration} segundos")
        if duration_index >= 0:
            self.duration_combo.setCurrentIndex(duration_index)
        aspect_ratio = str(self._preferences.value("generation/aspect_ratio", "9:16"))
        format_index = self.format_combo.findText(aspect_ratio)
        if format_index >= 0:
            self.format_combo.setCurrentIndex(format_index)
        geometry = self._preferences.value("window/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)

    def _save_preferences(self) -> None:
        self._preferences.setValue(
            "generation/duration",
            self.duration_combo.currentText().split()[0],
        )
        self._preferences.setValue("generation/aspect_ratio", self.format_combo.currentText())
        self._preferences.setValue("window/geometry", self.saveGeometry())
        self._preferences.sync()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self._pipeline_running():
            self._show_error("La producción sigue activa. Espera a que termine antes de cerrar.")
            event.ignore()
            return
        if self._jobs_thread is not None and self._jobs_thread.isRunning():
            self._show_error("ORION todavía está cargando los trabajos locales.")
            event.ignore()
            return
        self._save_preferences()
        event.accept()


def _status_name(status: ProductionJobStatus) -> str:
    names = {
        ProductionJobStatus.CREATED: "Creado",
        ProductionJobStatus.QUEUED: "En cola",
        ProductionJobStatus.RUNNING: "En curso",
        ProductionJobStatus.WAITING_FOR_RETRY: "Esperando",
        ProductionJobStatus.NEEDS_USER_ACTION: "Requiere acción",
        ProductionJobStatus.CANCEL_REQUESTED: "Cancelando",
        ProductionJobStatus.CANCELLED: "Cancelado",
        ProductionJobStatus.COMPLETED: "Completado",
        ProductionJobStatus.FAILED: "Fallido",
    }
    return names[status]


__all__ = ["OrionMainWindow", "STAGES"]
