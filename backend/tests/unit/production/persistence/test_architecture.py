"""Architectural boundaries preserved by production persistence."""

import ast
from pathlib import Path


def imported_roots(directory: Path) -> set[str]:
    roots: set[str] = set()
    for source_path in directory.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_domain_and_orchestration_do_not_import_sqlalchemy() -> None:
    source_root = Path(__file__).parents[4] / "src" / "production"

    assert "sqlalchemy" not in imported_roots(source_root / "domain")
    assert "sqlalchemy" not in imported_roots(source_root / "application" / "orchestration")


def test_persistence_has_no_api_editor_or_codex_imports() -> None:
    persistence = Path(__file__).parents[4] / "src" / "production" / "infrastructure" / "persistence"
    forbidden = {"fastapi", "davinci", "DaVinciResolveScript", "codex", "cv2", "ffmpeg"}

    assert imported_roots(persistence).isdisjoint(forbidden)
