"""Lightweight AST guards for production dependency direction."""

from __future__ import annotations

import ast
from pathlib import Path

PRODUCTION_ROOT = Path(__file__).resolve().parents[3] / "src" / "production"
PRODUCTION_PREFIX = "backend.src.production."


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _python_files() -> tuple[Path, ...]:
    return tuple(
        path
        for path in PRODUCTION_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _violations(
    files: tuple[Path, ...],
    forbidden_prefixes: tuple[str, ...],
) -> list[str]:
    violations: list[str] = []
    for path in files:
        for imported in _imports(path):
            if imported.startswith(forbidden_prefixes):
                relative = path.relative_to(PRODUCTION_ROOT).as_posix()
                violations.append(f"{relative}: {imported}")
    return sorted(violations)


def test_production_never_imports_test_code() -> None:
    forbidden = ("backend.tests", "tests.")
    assert _violations(_python_files(), forbidden) == []


def test_domain_contracts_do_not_import_adapters_or_runtime() -> None:
    domain_files = tuple(
        path
        for path in _python_files()
        if path.relative_to(PRODUCTION_ROOT).parts[0] == "domain"
        or "domain" in path.relative_to(PRODUCTION_ROOT).parts[:-1]
    )
    forbidden = (
        f"{PRODUCTION_PREFIX}infrastructure",
        f"{PRODUCTION_PREFIX}composition",
        f"{PRODUCTION_PREFIX}runtime",
        "backend.src.infrastructure",
        "httpx",
        "sqlalchemy",
        "fastapi",
    )
    assert _violations(domain_files, forbidden) == []


def test_application_core_does_not_import_runtime_or_infrastructure() -> None:
    application_files = tuple(
        path
        for path in _python_files()
        if path.relative_to(PRODUCTION_ROOT).parts[0] == "application"
    )
    forbidden = (
        f"{PRODUCTION_PREFIX}infrastructure",
        f"{PRODUCTION_PREFIX}composition",
        f"{PRODUCTION_PREFIX}runtime",
        "backend.src.infrastructure",
        "httpx",
        "sqlalchemy",
        "fastapi",
    )
    assert _violations(application_files, forbidden) == []


def test_provider_modules_do_not_import_pipeline_orchestration() -> None:
    provider_files = tuple(
        path
        for path in _python_files()
        if "providers" in path.relative_to(PRODUCTION_ROOT).parts[:-1]
    )
    forbidden = (
        f"{PRODUCTION_PREFIX}application.orchestration",
        f"{PRODUCTION_PREFIX}composition",
        f"{PRODUCTION_PREFIX}runtime.handlers",
    )
    assert _violations(provider_files, forbidden) == []


def test_simulated_providers_do_not_import_real_provider_transport() -> None:
    simulated_files = tuple(
        path
        for path in _python_files()
        if "simulated" in path.name
        and "providers" in path.relative_to(PRODUCTION_ROOT).parts[:-1]
    )
    forbidden = ("httpx", "openai", "backend.src.production.infrastructure.openai")
    assert _violations(simulated_files, forbidden) == []


def test_provider_neutral_asset_contexts_do_not_import_provider_adapters() -> None:
    files = tuple(
        path
        for path in _python_files()
        if path.relative_to(PRODUCTION_ROOT).parts[0]
        in {"asset_publishing", "binary_assets"}
    )
    forbidden = (
        f"{PRODUCTION_PREFIX}image_acquisition.providers",
        f"{PRODUCTION_PREFIX}video_clip_generation.providers",
    )
    assert _violations(files, forbidden) == []
