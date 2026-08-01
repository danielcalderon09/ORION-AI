"""Validate the public OpenRouter scripting-model research snapshot offline."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

SCHEMA_VERSION = "1.0.0"
EXPECTED_CATEGORIES = {
    "availability_routing",
    "context_fit",
    "cost",
    "latency_efficiency",
    "spanish_scripting",
    "structured_output",
}
OFFICIAL_HOST = "openrouter.ai"
MONEY_KEYS = {
    "input_price_usd_per_token",
    "output_price_usd_per_token",
    "request_price_usd",
}
FORBIDDEN_KEYS = {
    "account_balance",
    "account_id",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "private_usage_history",
    "secret",
}
FORBIDDEN_VALUES = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]+\b"),
    re.compile(r"[?&](?:key|token|signature|cookie)=", re.IGNORECASE),
)


class EvaluationValidationError(ValueError):
    """Raised when the research snapshot is unsafe or inconsistent."""


def _fail(message: str) -> NoReturn:
    raise EvaluationValidationError(message)


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    _fail(f"non-finite JSON number: {value}")


def _reject_float(value: str) -> NoReturn:
    _fail(f"JSON float is forbidden; use a Decimal string: {value}")


def load_strict_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise EvaluationValidationError("artifact must be strict UTF-8") from exc
    if raw.startswith("\ufeff"):
        _fail("artifact must not contain a BOM")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except json.JSONDecodeError as exc:
        raise EvaluationValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        _fail("artifact root must be an object")
    canonical = (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if raw != canonical:
        _fail("artifact must use canonical sorted JSON")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{name} must be an object")
    return value


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _fail(f"{name} must be an array")
    return value


def _decimal(value: Any, name: str, *, nullable: bool = False) -> Decimal | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        _fail(f"{name} must be a Decimal string or null")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise EvaluationValidationError(f"{name} is not Decimal-safe") from exc
    if not parsed.is_finite():
        _fail(f"{name} must be finite")
    return parsed


def _iso_date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        _fail(f"{name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EvaluationValidationError(f"{name} must be an ISO date") from exc


def _utc_time(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail(f"{name} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationValidationError(f"{name} must be a UTC timestamp") from exc
    if parsed.utcoffset() is None:
        _fail(f"{name} must be timezone-aware")
    return parsed


def _unique(items: Sequence[Any], key: str, name: str) -> set[str]:
    values: set[str] = set()
    for index, raw in enumerate(items):
        item = _mapping(raw, f"{name}[{index}]")
        value = item.get(key)
        if not isinstance(value, str) or not value:
            _fail(f"{name}[{index}].{key} must be non-empty")
        if value in values:
            _fail(f"duplicate {name} {key}: {value}")
        values.add(value)
    return values


def _validate_sources(document: Mapping[str, Any]) -> set[str]:
    sources = _sequence(document.get("sources"), "sources")
    ids = _unique(sources, "source_id", "sources")
    for index, raw in enumerate(sources):
        source = _mapping(raw, f"sources[{index}]")
        if source.get("official") is not True:
            _fail("every source must be official")
        _iso_date(source.get("accessed_on"), f"sources[{index}].accessed_on")
        url = source.get("url")
        if not isinstance(url, str) or urlsplit(url).hostname != OFFICIAL_HOST:
            _fail("every source must use the official OpenRouter host")
        if urlsplit(url).scheme != "https":
            _fail("every source must use HTTPS")
    return ids


def _validate_models(
    document: Mapping[str, Any], source_ids: set[str]
) -> tuple[dict[str, Mapping[str, Any]], set[str]]:
    models = _sequence(document.get("shortlisted_models"), "shortlisted_models")
    _unique(models, "model_id", "shortlisted_models")
    by_id: dict[str, Mapping[str, Any]] = {}
    free_ids: set[str] = set()
    for index, raw in enumerate(models):
        model = _mapping(raw, f"shortlisted_models[{index}]")
        model_id = str(model["model_id"])
        by_id[model_id] = model
        evidence = _sequence(model.get("source_ids"), f"{model_id}.source_ids")
        if not evidence or any(item not in source_ids for item in evidence):
            _fail(f"{model_id} must reference known official evidence")
        for key in (
            "input_price_usd_per_token",
            "output_price_usd_per_token",
            "request_price_usd",
        ):
            amount = _decimal(model.get(key), f"{model_id}.{key}", nullable=True)
            if amount is not None and amount < 0:
                _fail(f"{model_id}.{key} must not be negative")
        if model.get("availability") not in {"paid", "free"}:
            _fail(f"{model_id} availability must be paid or free")
        free_variant = model.get("free_variant") is True
        if model.get("availability") == "free":
            free_ids.add(model_id)
            if not free_variant or not model_id.endswith(":free"):
                _fail(f"free candidate is not explicitly labeled: {model_id}")
        if not isinstance(model.get("risks"), list) or not model["risks"]:
            _fail(f"{model_id} must document risks")
    return by_id, free_ids


def _validate_scores(
    document: Mapping[str, Any], models: Mapping[str, Mapping[str, Any]], source_ids: set[str]
) -> None:
    weights = _mapping(document.get("scoring_weights"), "scoring_weights")
    if set(weights) != EXPECTED_CATEGORIES:
        _fail("scoring weights must match the six evaluation categories")
    parsed_weights = {
        key: _decimal(value, f"scoring_weights.{key}") for key, value in weights.items()
    }
    if sum(value for value in parsed_weights.values() if value is not None) != 100:
        _fail("scoring weights must sum to 100")
    scores = _sequence(document.get("scores"), "scores")
    scored = _unique(scores, "model_id", "scores")
    if scored != set(models):
        _fail("every shortlisted model must have exactly one score")
    for raw in scores:
        score = _mapping(raw, "score")
        model_id = str(score["model_id"])
        categories = _mapping(score.get("categories"), f"{model_id}.categories")
        if set(categories) != EXPECTED_CATEGORIES:
            _fail(f"{model_id} must score every category")
        raw_sum = Decimal(0)
        weighted_sum = Decimal(0)
        adjustment_sum = Decimal(0)
        penalty_sum = Decimal(0)
        final_sum = Decimal(0)
        for category, raw_category in categories.items():
            item = _mapping(raw_category, f"{model_id}.{category}")
            evidence = _sequence(item.get("evidence_source_ids"), "score evidence")
            if not evidence or any(source not in source_ids for source in evidence):
                _fail(f"{model_id}.{category} lacks official evidence")
            for text_key in ("judgment", "uncertainty"):
                if not isinstance(item.get(text_key), str) or not item[text_key].strip():
                    _fail(f"{model_id}.{category}.{text_key} is required")
            raw_value = _decimal(item.get("raw_score"), "raw_score")
            confidence = _decimal(item.get("confidence"), "confidence")
            penalty = _decimal(item.get("penalty"), "penalty")
            weighted = _decimal(item.get("weighted_contribution"), "weighted_contribution")
            adjustment = _decimal(item.get("confidence_adjustment"), "confidence_adjustment")
            final = _decimal(item.get("final_weighted_contribution"), "final contribution")
            assert None not in {raw_value, confidence, penalty, weighted, adjustment, final}
            if not Decimal(0) <= raw_value <= Decimal(10):
                _fail(f"{model_id}.{category} score is outside 0..10")
            if not Decimal(0) <= confidence <= Decimal(1):
                _fail(f"{model_id}.{category} confidence is outside 0..1")
            expected_weighted = raw_value * parsed_weights[category] / Decimal(100)
            expected_adjustment = expected_weighted * (Decimal(1) - confidence)
            expected_final = expected_weighted - expected_adjustment - penalty
            if (weighted, adjustment, final) != (
                expected_weighted,
                expected_adjustment,
                expected_final,
            ):
                _fail(f"{model_id}.{category} score calculation is not reproducible")
            raw_sum += raw_value
            weighted_sum += weighted
            adjustment_sum += adjustment
            penalty_sum += penalty
            final_sum += final
        expected_overall = {
            "confidence_adjustment": adjustment_sum,
            "final_result": final_sum,
            "raw_score": raw_sum / Decimal(len(EXPECTED_CATEGORIES)),
            "risk_penalty": penalty_sum,
            "weighted_score": weighted_sum,
        }
        for key, expected in expected_overall.items():
            if _decimal(score.get(key), f"{model_id}.{key}") != expected:
                _fail(f"{model_id}.{key} is not reproducible")
        if _decimal(models[model_id].get("score"), f"{model_id}.score") != final_sum:
            _fail(f"{model_id} shortlist score differs from its final result")


def _validate_costs(document: Mapping[str, Any], models: Mapping[str, Mapping[str, Any]]) -> None:
    assumptions = _mapping(document.get("token_assumptions"), "token_assumptions")
    input_range = _mapping(assumptions.get("input_tokens"), "input_tokens")
    outputs = _mapping(assumptions.get("output_tokens"), "output_tokens")
    input_low = Decimal(input_range.get("low"))
    input_high = Decimal(input_range.get("high"))
    estimates = _sequence(document.get("cost_estimates"), "cost_estimates")
    if _unique(estimates, "model_id", "cost_estimates") != set(models):
        _fail("every shortlisted model must have one cost estimate")
    for raw in estimates:
        estimate = _mapping(raw, "cost estimate")
        model_id = str(estimate["model_id"])
        model = models[model_id]
        input_price = _decimal(model.get("input_price_usd_per_token"), "input price") or Decimal(0)
        output_price = _decimal(model.get("output_price_usd_per_token"), "output price") or Decimal(
            0
        )
        request_price = _decimal(model.get("request_price_usd"), "request price", nullable=True)
        request_price = request_price or Decimal(0)
        durations = _mapping(estimate.get("durations"), f"{model_id}.durations")
        if set(durations) != {"15", "30", "60"}:
            _fail(f"{model_id} must estimate 15, 30 and 60 seconds")
        computed_30: tuple[Decimal, Decimal] | None = None
        for duration, raw_range in durations.items():
            token_range = _mapping(outputs.get(duration), f"output_tokens.{duration}")
            amounts = _mapping(raw_range, f"{model_id}.durations.{duration}")
            expected_low = (
                input_low * input_price + Decimal(token_range["low"]) * output_price + request_price
            )
            expected_high = (
                input_high * input_price
                + Decimal(token_range["high"]) * output_price
                + request_price
            )
            actual = (
                _decimal(amounts.get("low"), "cost low"),
                _decimal(amounts.get("high"), "cost high"),
            )
            if actual != (expected_low, expected_high):
                _fail(f"{model_id} {duration}-second cost is not reproducible")
            if duration == "30":
                computed_30 = (expected_low, expected_high)
        assert computed_30 is not None
        for count, key in ((100, "one_hundred_30_second"), (1000, "one_thousand_30_second")):
            amounts = _mapping(estimate.get(key), f"{model_id}.{key}")
            if (
                _decimal(amounts.get("low"), "scaled low"),
                _decimal(amounts.get("high"), "scaled high"),
            ) != (computed_30[0] * count, computed_30[1] * count):
                _fail(f"{model_id} {key} cost is not reproducible")


def _validate_recommendation(
    document: Mapping[str, Any], model_ids: set[str], free_ids: set[str]
) -> None:
    recommendation = _mapping(document.get("recommendation"), "recommendation")
    if recommendation.get("status") != "proposed":
        _fail("recommendation status must be proposed")
    for key in (
        "primary_economical_model_id",
        "quality_fallback_model_id",
        "free_test_model_id",
    ):
        if recommendation.get(key) not in model_ids:
            _fail(f"{key} references an unknown shortlisted model")
    if recommendation.get("free_test_model_id") not in free_ids:
        _fail("free test recommendation must reference a labeled free candidate")


def _scan_security(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key.casefold() in FORBIDDEN_KEYS:
                _fail(f"forbidden private key at {path}.{key}")
            if key in MONEY_KEYS and child is not None and not isinstance(child, str):
                _fail(f"money must be a Decimal string at {path}.{key}")
            _scan_security(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _scan_security(child, f"{path}[{index}]")
    elif isinstance(value, str) and any(pattern.search(value) for pattern in FORBIDDEN_VALUES):
        _fail(f"credential-like value at {path}")


def validate_artifact(path: Path) -> dict[str, Any]:
    document = load_strict_json(path)
    if document.get("schema_version") != SCHEMA_VERSION:
        _fail("unsupported evaluation schema version")
    researched = _utc_time(document.get("researched_at"), "researched_at")
    accessed = _iso_date(document.get("source_access_date"), "source_access_date")
    review = _iso_date(document.get("review_after"), "review_after")
    if researched.date() != accessed or review <= accessed:
        _fail("research and review dates are stale or inconsistent")
    if (
        not isinstance(document.get("discovered_model_count"), int)
        or document["discovered_model_count"] < 1
    ):
        _fail("discovered_model_count must be positive")
    eligible_count = document.get("eligible_model_count")
    if (
        not isinstance(eligible_count, int)
        or eligible_count < 1
        or eligible_count > document["discovered_model_count"]
    ):
        _fail("eligible_model_count must be positive and bounded by discovery")
    source_ids = _validate_sources(document)
    models, free_ids = _validate_models(document, source_ids)
    _validate_scores(document, models, source_ids)
    _validate_costs(document, models)
    _validate_recommendation(document, set(models), free_ids)
    _scan_security(document)
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("docs/research/openrouter-scripting-model-evaluation.json"),
    )
    args = parser.parse_args()
    document = validate_artifact(args.path)
    print(
        "OpenRouter scripting evaluation valid: "
        f"{len(document['shortlisted_models'])} shortlisted models"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
