from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_validator() -> ModuleType:
    path = _repository_root() / "scripts" / "validate_tts_provider_evaluation.py"
    spec = importlib.util.spec_from_file_location("tts_research_validator", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_tts_provider_research_artifact_is_strict_safe_and_reproducible() -> None:
    validator = _load_validator()
    path = _repository_root() / "docs" / "research" / "tts-provider-evaluation.json"

    document = validator.validate_artifact(path)

    assert document["recommendation"]["decision"] == "no_provider_selected_yet"
    assert document["recommendation"]["primary_candidate"] == "azure"
    assert document["recommendation"]["secondary_candidate"] == "google"


def test_validator_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    validator = _load_validator()
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"1.0.0","schema_version":"1.0.0"}\n', encoding="utf-8")

    with pytest.raises(validator.ResearchArtifactValidationError, match="duplicate JSON key"):
        validator.load_strict_json(path)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1.25"])
def test_validator_rejects_non_decimal_json_numbers(tmp_path: Path, value: str) -> None:
    validator = _load_validator()
    path = tmp_path / "unsafe-number.json"
    path.write_text(f'{{"value":{value}}}\n', encoding="utf-8")

    with pytest.raises(validator.ResearchArtifactValidationError):
        validator.load_strict_json(path)
