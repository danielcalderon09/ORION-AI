"""Import-boundary test for pure production orchestration."""

import ast
from pathlib import Path


def test_orchestration_has_no_infrastructure_or_tool_imports() -> None:
    orchestration_dir = (
        Path(__file__).parents[4] / "src" / "production" / "application" / "orchestration"
    )
    forbidden = {
        "fastapi",
        "sqlalchemy",
        "ffmpeg",
        "cv2",
        "davinci",
        "DaVinciResolveScript",
        "codex",
    }
    imported_roots: set[str] = set()

    for source_path in orchestration_dir.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])

    assert imported_roots.isdisjoint(forbidden)
