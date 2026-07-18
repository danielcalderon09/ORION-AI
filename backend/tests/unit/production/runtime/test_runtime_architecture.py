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
