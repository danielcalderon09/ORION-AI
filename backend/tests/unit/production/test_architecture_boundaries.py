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
    return tuple(path for path in PRODUCTION_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


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
        if "simulated" in path.name and "providers" in path.relative_to(PRODUCTION_ROOT).parts[:-1]
    )
    forbidden = ("httpx", "openai", "backend.src.production.infrastructure.openai")
    assert _violations(simulated_files, forbidden) == []


def test_provider_neutral_asset_contexts_do_not_import_provider_adapters() -> None:
    files = tuple(
        path
        for path in _python_files()
        if path.relative_to(PRODUCTION_ROOT).parts[0] in {"asset_publishing", "binary_assets"}
    )
    forbidden = (
        f"{PRODUCTION_PREFIX}image_acquisition.providers",
        f"{PRODUCTION_PREFIX}video_clip_generation.providers",
    )
    assert _violations(files, forbidden) == []


def test_speech_contracts_do_not_import_runtime_composition_or_transports() -> None:
    speech = PRODUCTION_ROOT / "speech_generation"
    files = tuple(
        speech / name
        for name in (
            "configuration.py",
            "duration.py",
            "models.py",
            "segment_builder.py",
            "wav.py",
        )
    )
    forbidden = (
        f"{PRODUCTION_PREFIX}runtime",
        f"{PRODUCTION_PREFIX}composition",
        "backend.src.infrastructure",
        "httpx",
        "openai",
        "sqlalchemy",
        "fastapi",
    )
    assert _violations(files, forbidden) == []


def test_simulated_speech_provider_has_no_network_or_subprocess_dependency() -> None:
    files = (PRODUCTION_ROOT / "speech_generation" / "providers" / "simulated_provider.py",)
    forbidden = (
        "httpx",
        "openai",
        "subprocess",
        f"{PRODUCTION_PREFIX}video_clip_generation",
        f"{PRODUCTION_PREFIX}image_acquisition",
    )
    assert _violations(files, forbidden) == []


def test_speech_handler_uses_ports_not_concrete_provider_adapters() -> None:
    files = (PRODUCTION_ROOT / "speech_generation" / "handler.py",)
    forbidden = (f"{PRODUCTION_PREFIX}speech_generation.providers",)
    assert _violations(files, forbidden) == []


def test_only_composition_selects_simulated_speech_provider() -> None:
    provider_prefix = f"{PRODUCTION_PREFIX}speech_generation.providers"
    violations: list[str] = []
    for path in _python_files():
        relative = path.relative_to(PRODUCTION_ROOT)
        if relative.parts[0] == "composition" or "providers" in relative.parts[:-1]:
            continue
        if any(imported.startswith(provider_prefix) for imported in _imports(path)):
            violations.append(relative.as_posix())
    assert violations == []


def test_shared_script_reader_does_not_reverse_depend_on_speech() -> None:
    files = (PRODUCTION_ROOT / "infrastructure" / "durable_production_script_reader.py",)
    forbidden = (f"{PRODUCTION_PREFIX}speech_generation",)
    assert _violations(files, forbidden) == []


def test_remote_speech_contracts_have_no_transport_or_runtime_dependency() -> None:
    speech = PRODUCTION_ROOT / "speech_generation"
    files = tuple(
        speech / name
        for name in (
            "billable_gate.py",
            "cost.py",
            "fingerprinting.py",
            "remote_capabilities.py",
            "remote_models.py",
            "remote_ports.py",
            "remote_recovery.py",
            "voice_selection.py",
        )
    )
    forbidden = (
        f"{PRODUCTION_PREFIX}composition",
        f"{PRODUCTION_PREFIX}runtime.handlers",
        f"{PRODUCTION_PREFIX}speech_generation.providers",
        "backend.src.infrastructure",
        "httpx",
        "requests",
        "aiohttp",
        "urllib",
        "socket",
        "openai",
        "sqlalchemy",
        "fastapi",
    )
    assert _violations(files, forbidden) == []


def test_disabled_remote_speech_provider_has_no_transport_or_sdk_dependency() -> None:
    files = (PRODUCTION_ROOT / "speech_generation" / "providers" / "disabled_remote_provider.py",)
    forbidden = (
        "httpx",
        "requests",
        "aiohttp",
        "urllib",
        "socket",
        "subprocess",
        "openai",
        "openrouter",
        "elevenlabs",
        "azure",
        "google",
        "boto3",
        "botocore",
    )
    assert _violations(files, forbidden) == []


def test_simulated_speech_provider_does_not_import_remote_preparation() -> None:
    path = PRODUCTION_ROOT / "speech_generation" / "providers" / "simulated_provider.py"
    imported = _imports(path)
    assert not any(
        name.startswith(f"{PRODUCTION_PREFIX}speech_generation.remote")
        or name.startswith(f"{PRODUCTION_PREFIX}speech_generation.billable")
        or name.startswith(f"{PRODUCTION_PREFIX}speech_generation.cost")
        for name in imported
    )


def test_remote_speech_store_is_provider_neutral() -> None:
    files = (
        PRODUCTION_ROOT / "speech_generation" / "remote_job_store.py",
        PRODUCTION_ROOT / "speech_generation" / "remote_reconciliation.py",
    )
    forbidden = (
        f"{PRODUCTION_PREFIX}speech_generation.providers",
        f"{PRODUCTION_PREFIX}video_clip_generation.providers",
        "httpx",
        "sqlalchemy",
    )
    assert _violations(files, forbidden) == []


def test_active_speech_handler_has_no_remote_provider_route() -> None:
    path = PRODUCTION_ROOT / "speech_generation" / "handler.py"
    imported = _imports(path)
    assert not any(
        name.startswith(f"{PRODUCTION_PREFIX}speech_generation.remote")
        or name.endswith("disabled_remote_provider")
        for name in imported
    )


def test_speech_context_has_no_real_provider_sdk_dependency() -> None:
    speech_files = tuple((PRODUCTION_ROOT / "speech_generation").rglob("*.py"))
    forbidden = (
        "openai",
        "openrouter",
        "elevenlabs",
        "azure",
        "google.cloud",
        "boto3",
        "botocore",
    )
    assert _violations(speech_files, forbidden) == []


def test_only_composition_selects_concrete_speech_capability_source() -> None:
    source_module = f"{PRODUCTION_PREFIX}speech_generation.capability_sources"
    violations: list[str] = []
    for path in _python_files():
        relative = path.relative_to(PRODUCTION_ROOT)
        if (
            relative.parts[0] == "composition"
            or relative.as_posix() == "speech_generation/capability_sources.py"
        ):
            continue
        if any(imported.startswith(source_module) for imported in _imports(path)):
            violations.append(relative.as_posix())
    assert violations == []
