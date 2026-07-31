"""Preparation-only architecture boundaries."""

import ast
from pathlib import Path

import pytest

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.models import RendererKind

ROOT = Path(__file__).resolve().parents[5]
RENDERING_ROOT = ROOT / "backend" / "src" / "production" / "rendering"


def test_rendering_context_has_no_execution_or_transport_imports() -> None:
    forbidden = {
        "subprocess",
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "moviepy",
        "opentimelineio",
        "blender",
        "sqlalchemy",
    }
    violations: list[str] = []
    for path in RENDERING_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.names[0].name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        if imports & forbidden:
            violations.append(path.name)
    assert violations == []
    subprocess_users = tuple(
        path.name
        for path in RENDERING_ROOT.rglob("*.py")
        if "create_subprocess" in path.read_text(encoding="utf-8").lower()
    )
    assert subprocess_users == ("process_runner.py",)


def test_rendering_domain_does_not_depend_on_infrastructure_or_runtime() -> None:
    domain_files = (
        RENDERING_ROOT / "models.py",
        RENDERING_ROOT / "ports.py",
        RENDERING_ROOT / "fingerprints.py",
        RENDERING_ROOT / "request_builder.py",
        RENDERING_ROOT / "recovery.py",
    )
    content = "\n".join(path.read_text(encoding="utf-8") for path in domain_files)
    assert "backend.src.infrastructure" not in content
    assert "backend.src.production.composition" not in content
    assert "backend.src.production.runtime" not in content


def test_configuration_activates_only_supported_local_renderers(tmp_path: Path) -> None:
    values = {
        "_env_file": None,
        "ORION_HOME": tmp_path / "home",
        "MODELS_DIR": tmp_path / "models",
        "PROJECTS_DIR": tmp_path / "projects",
        "TEMP_DIR": tmp_path / "temp",
    }
    assert Settings(**values, ORION_RENDERER="ffmpeg").ORION_RENDERER == "ffmpeg"
    for value in ("davinci_resolve", "third_party"):
        with pytest.raises(ValueError):
            Settings(**values, ORION_RENDERER=value)
    assert RenderingConfiguration().renderer is RendererKind.DRY_RUN


def test_no_new_final_media_artifact_type_exists() -> None:
    assert ArtifactType.LOCAL_RENDER_REQUEST.value == "local_render_request"
    assert ArtifactType.RENDER_EXECUTION_MANIFEST.value == "render_execution_manifest"
    assert ArtifactType.FFMPEG_EXECUTION_PLAN.value == "ffmpeg_execution_plan"
    assert "final_video" not in {item.value for item in ArtifactType}
    assert "rendered_video" not in {item.value for item in ArtifactType}
