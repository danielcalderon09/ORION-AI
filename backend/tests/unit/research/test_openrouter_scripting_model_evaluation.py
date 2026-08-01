from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _root() -> Path:
    return Path(__file__).resolve().parents[4]


def _validator() -> ModuleType:
    path = _root() / "scripts" / "validate_openrouter_scripting_model_evaluation.py"
    spec = importlib.util.spec_from_file_location("openrouter_scripting_research", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _artifact() -> Path:
    return _root() / "docs" / "research" / "openrouter-scripting-model-evaluation.json"


def _document() -> dict[str, Any]:
    return json.loads(_artifact().read_text(encoding="utf-8"))


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_public_evaluation_artifact_is_valid_and_recommends_only_shortlisted_models() -> None:
    document = _validator().validate_artifact(_artifact())
    assert document["discovered_model_count"] == 336
    assert document["recommendation"] == {
        "free_test_model_id": "google/gemma-4-26b-a4b-it:free",
        "primary_economical_model_id": "google/gemini-2.5-flash-lite",
        "quality_fallback_model_id": "openai/gpt-4.1-mini",
        "rationale": document["recommendation"]["rationale"],
        "status": "proposed",
    }


@pytest.mark.parametrize(
    ("collection", "identifier"),
    [("shortlisted_models", "model_id"), ("sources", "source_id")],
)
def test_duplicate_model_or_source_id_is_rejected(
    tmp_path: Path, collection: str, identifier: str
) -> None:
    validator = _validator()
    document = _document()
    duplicate = copy.deepcopy(document[collection][0])
    assert duplicate[identifier]
    document[collection].append(duplicate)
    path = tmp_path / "duplicate.json"
    _write(path, document)
    with pytest.raises(validator.EvaluationValidationError, match="duplicate"):
        validator.validate_artifact(path)


def test_missing_model_evidence_is_rejected(tmp_path: Path) -> None:
    validator = _validator()
    document = _document()
    document["shortlisted_models"][0]["source_ids"] = []
    path = tmp_path / "missing-evidence.json"
    _write(path, document)
    with pytest.raises(validator.EvaluationValidationError, match="evidence"):
        validator.validate_artifact(path)


@pytest.mark.parametrize("unsafe", [0.0001, "not-a-decimal"])
def test_float_or_malformed_price_is_rejected(tmp_path: Path, unsafe: object) -> None:
    validator = _validator()
    document = _document()
    document["shortlisted_models"][0]["input_price_usd_per_token"] = unsafe
    path = tmp_path / "unsafe-price.json"
    _write(path, document)
    with pytest.raises(validator.EvaluationValidationError):
        validator.validate_artifact(path)


def test_weights_must_sum_to_one_hundred(tmp_path: Path) -> None:
    validator = _validator()
    document = _document()
    document["scoring_weights"]["cost"] = "19"
    path = tmp_path / "bad-weights.json"
    _write(path, document)
    with pytest.raises(validator.EvaluationValidationError, match="sum to 100"):
        validator.validate_artifact(path)


def test_score_must_be_bounded_and_reproducible(tmp_path: Path) -> None:
    validator = _validator()
    document = _document()
    document["scores"][0]["categories"]["cost"]["raw_score"] = "11"
    path = tmp_path / "bad-score.json"
    _write(path, document)
    with pytest.raises(validator.EvaluationValidationError, match="outside"):
        validator.validate_artifact(path)


def test_cost_estimate_must_be_reproducible(tmp_path: Path) -> None:
    validator = _validator()
    document = _document()
    document["cost_estimates"][0]["durations"]["30"]["high"] = "9.99"
    path = tmp_path / "bad-cost.json"
    _write(path, document)
    with pytest.raises(validator.EvaluationValidationError, match="not reproducible"):
        validator.validate_artifact(path)


def test_recommendation_must_reference_shortlist(tmp_path: Path) -> None:
    validator = _validator()
    document = _document()
    document["recommendation"]["primary_economical_model_id"] = "unknown/model"
    path = tmp_path / "unknown-recommendation.json"
    _write(path, document)
    with pytest.raises(validator.EvaluationValidationError, match="unknown"):
        validator.validate_artifact(path)


def test_free_candidate_must_be_explicitly_labeled(tmp_path: Path) -> None:
    validator = _validator()
    document = _document()
    free = next(model for model in document["shortlisted_models"] if model["free_variant"])
    free["free_variant"] = False
    path = tmp_path / "unlabeled-free.json"
    _write(path, document)
    with pytest.raises(validator.EvaluationValidationError, match="explicitly labeled"):
        validator.validate_artifact(path)


def test_review_date_must_follow_access_date(tmp_path: Path) -> None:
    validator = _validator()
    document = _document()
    document["review_after"] = document["source_access_date"]
    path = tmp_path / "stale.json"
    _write(path, document)
    with pytest.raises(validator.EvaluationValidationError, match="stale"):
        validator.validate_artifact(path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("note", "sk-or-v1-" + "abcdefghijklmnop"),
        ("note", "Authorization: " + "Bearer " + "abcdefghijklmnop"),
        ("account_" + "balance", "0"),
    ],
)
def test_private_or_credential_like_data_is_rejected(tmp_path: Path, key: str, value: str) -> None:
    validator = _validator()
    document = _document()
    document["safe_metadata"][key] = value
    path = tmp_path / "private.json"
    _write(path, document)
    with pytest.raises(validator.EvaluationValidationError):
        validator.validate_artifact(path)


def test_canonical_sorted_json_is_required(tmp_path: Path) -> None:
    validator = _validator()
    path = tmp_path / "noncanonical.json"
    path.write_text(json.dumps(_document(), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(validator.EvaluationValidationError, match="canonical"):
        validator.validate_artifact(path)


def test_duplicate_json_key_is_rejected_before_validation(tmp_path: Path) -> None:
    validator = _validator()
    path = tmp_path / "duplicate-key.json"
    path.write_text('{"schema_version":"1.0.0","schema_version":"1.0.0"}\n')
    with pytest.raises(validator.EvaluationValidationError, match="duplicate JSON key"):
        validator.load_strict_json(path)
