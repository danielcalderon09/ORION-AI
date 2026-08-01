import ast
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[3] / "src" / "desktop"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_desktop_has_no_media_execution_or_provider_dependency() -> None:
    imports = set().union(*(_imports(path) for path in DESKTOP_ROOT.glob("*.py")))
    forbidden = (
        "subprocess",
        "backend.src.production.rendering",
        "backend.src.production.render_validation.probe",
        "backend.src.production.image_acquisition.providers",
        "backend.src.production.video_clip_generation.providers",
        "backend.src.production.speech_generation.providers",
        "httpx",
        "requests",
    )
    assert not any(name.startswith(forbidden) for name in imports)


def test_main_window_depends_on_backend_contract_not_composition() -> None:
    imports = _imports(DESKTOP_ROOT / "main_window.py")
    assert "backend.src.production.composition" not in imports
    assert "backend.src.production.infrastructure" not in imports
