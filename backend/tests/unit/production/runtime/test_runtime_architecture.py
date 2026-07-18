from pathlib import Path

from backend.src.infrastructure.config.settings import Settings

ROOT = Path(__file__).resolve().parents[5]


def test_runtime_has_no_forbidden_runtime_dependencies() -> None:
    runtime = ROOT / "backend" / "src" / "production" / "runtime"
    source = "\n".join(path.read_text(encoding="utf-8") for path in runtime.rglob("*.py"))
    forbidden = ("fastapi", "davinci", "codex", "ffmpeg", "cv2", "opencv")
    assert not any(token in source.lower() for token in forbidden)


def test_feature_flag_remains_disabled() -> None:
    assert Settings().ORION_PROMPT_VIDEO_ENABLED is False


def test_clip_controllers_are_not_runtime_dependencies() -> None:
    runtime = ROOT / "backend" / "src" / "production" / "runtime"
    source = "\n".join(path.read_text(encoding="utf-8") for path in runtime.rglob("*.py"))
    assert "video_controller" not in source
    assert "clip_controller" not in source


def test_worker_and_lease_manager_do_not_import_sqlalchemy_models() -> None:
    runtime = ROOT / "backend" / "src" / "production" / "runtime"
    worker = (runtime / "worker.py").read_text(encoding="utf-8")
    manager = (runtime / "leases" / "lease_manager.py").read_text(encoding="utf-8")
    compatibility = (runtime / "lease_manager.py").read_text(encoding="utf-8")
    for source in (worker, manager, compatibility):
        assert "from sqlalchemy" not in source.lower()
        assert "import sqlalchemy" not in source.lower()
        assert "persistence.models" not in source
    assert "select(" not in worker
    assert "update(" not in worker
    assert "delete(" not in worker


def test_sql_is_encapsulated_by_runtime_adapters() -> None:
    runtime = ROOT / "backend" / "src" / "production" / "runtime"
    worker = (runtime / "worker.py").read_text(encoding="utf-8")
    reader = (runtime / "runtime_state_reader.py").read_text(encoding="utf-8")
    repository = (runtime / "leases" / "sqlalchemy_lease_repository.py").read_text(
        encoding="utf-8"
    )
    assert "select(" not in worker
    assert "select(" in reader
    assert "sqlite_insert" in repository


def test_handlers_do_not_import_persistence() -> None:
    handlers = ROOT / "backend" / "src" / "production" / "runtime" / "handlers"
    source = "\n".join(path.read_text(encoding="utf-8") for path in handlers.rglob("*.py"))
    assert "infrastructure.persistence" not in source
    assert "sqlalchemy" not in source.lower()
