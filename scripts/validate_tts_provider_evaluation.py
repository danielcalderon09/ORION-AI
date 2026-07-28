"""Validate the dated, public ORION TTS provider research artifact."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

SUPPORTED_SCHEMA_VERSIONS = {"1.0.0"}
EXPECTED_WEIGHT_TOTAL = 100
SCORE_QUANTUM = Decimal("0.1")
ALLOWED_COMPATIBILITY = {
    "directly_supported",
    "provider_does_not_support",
    "requires_contract_extension",
    "supported_with_adapter",
    "unknown_from_official_documentation",
}
OFFICIAL_SOURCE_HOSTS = {
    "aws_polly": {"aws.amazon.com", "docs.aws.amazon.com"},
    "azure": {
        "azure.microsoft.com",
        "azure.status.microsoft",
        "learn.microsoft.com",
        "www.microsoft.com",
    },
    "elevenlabs": {"elevenlabs.io", "status.elevenlabs.io"},
    "google": {"cloud.google.com"},
    "openai": {
        "developers.openai.com",
        "help.openai.com",
        "openai.com",
        "platform.openai.com",
        "status.openai.com",
    },
}
SENSITIVE_KEY_FRAGMENTS = {
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "signed_url",
    "token",
}
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"[?&](?:signature|sig|token|key)=", re.IGNORECASE),
)


class ResearchArtifactValidationError(ValueError):
    """Raised when the research artifact is unsafe or internally inconsistent."""


def _fail(message: str) -> NoReturn:
    raise ResearchArtifactValidationError(message)


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
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
    """Load strict JSON while rejecting duplicates, floats, and non-finite values."""

    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ResearchArtifactValidationError("artifact must be strict UTF-8") from exc
    if raw.startswith("\ufeff"):
        _fail("artifact must not contain a UTF-8 BOM")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except json.JSONDecodeError as exc:
        raise ResearchArtifactValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        _fail("artifact root must be an object")
    return value


def _as_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{name} must be an object")
    return value


def _as_sequence(value: Any, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _fail(f"{name} must be an array")
    return value


def _parse_decimal(value: Any, name: str) -> Decimal:
    if not isinstance(value, str):
        _fail(f"{name} must be a Decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ResearchArtifactValidationError(f"{name} is not Decimal-safe") from exc
    if not result.is_finite():
        _fail(f"{name} must be finite")
    return result


def _parse_date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        _fail(f"{name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ResearchArtifactValidationError(f"{name} must be an ISO date") from exc


def _parse_utc_datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        _fail(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchArtifactValidationError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(f"{name} must be timezone-aware")
    return parsed


def _require_unique_ids(items: Sequence[Any], name: str) -> set[str]:
    seen: set[str] = set()
    for index, raw_item in enumerate(items):
        item = _as_mapping(raw_item, f"{name}[{index}]")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            _fail(f"{name}[{index}].id must be non-empty")
        if item_id in seen:
            _fail(f"duplicate {name} id: {item_id}")
        seen.add(item_id)
    return seen


def _validate_sources(sources: Sequence[Any]) -> set[str]:
    source_ids = _require_unique_ids(sources, "sources")
    for index, raw_source in enumerate(sources):
        source = _as_mapping(raw_source, f"sources[{index}]")
        _parse_date(source.get("accessed_at"), f"sources[{index}].accessed_at")
        if source.get("official") is not True:
            _fail(f"sources[{index}] is not marked official")
        if source.get("source_kind") != "official_primary":
            _fail(f"sources[{index}] must be distinguished as official_primary")
        url = source.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            _fail(f"sources[{index}].url must be public HTTPS")
        provider = source.get("provider")
        allowed_hosts = OFFICIAL_SOURCE_HOSTS.get(provider)
        if allowed_hosts is None:
            _fail(f"sources[{index}].provider is not evaluated")
        if urlsplit(url).hostname not in allowed_hosts:
            _fail(f"sources[{index}].url is not an allowlisted official provider host")
        publication_date = source.get("publication_or_update_date")
        if publication_date is not None:
            _parse_date(
                publication_date,
                f"sources[{index}].publication_or_update_date",
            )
    return source_ids


def _validate_material_findings(
    providers: Sequence[Any],
    source_ids: set[str],
) -> set[str]:
    provider_ids = _require_unique_ids(providers, "providers")
    for provider_index, raw_provider in enumerate(providers):
        provider = _as_mapping(raw_provider, f"providers[{provider_index}]")
        findings = _as_sequence(
            provider.get("findings"),
            f"providers[{provider_index}].findings",
        )
        if not findings:
            _fail(f"providers[{provider_index}] has no material findings")
        for finding_index, raw_finding in enumerate(findings):
            finding = _as_mapping(
                raw_finding,
                f"providers[{provider_index}].findings[{finding_index}]",
            )
            references = _as_sequence(
                finding.get("source_ids"),
                f"providers[{provider_index}].findings[{finding_index}].source_ids",
            )
            if not references:
                _fail("every material finding must reference at least one source")
            unknown = {value for value in references if value not in source_ids}
            if unknown:
                _fail(f"material finding references unknown sources: {sorted(unknown)}")
            if finding.get("evidence_type") not in {"explicit", "inferred"}:
                _fail("material finding must distinguish explicit evidence from inference")
        compatibility = _as_mapping(
            provider.get("compatibility"),
            f"providers[{provider_index}].compatibility",
        )
        for contract, classification in compatibility.items():
            if classification not in ALLOWED_COMPATIBILITY:
                _fail(
                    f"invalid compatibility classification for "
                    f"{provider.get('id')}.{contract}: {classification}"
                )
    return provider_ids


def _validate_scores(
    criteria: Sequence[Any],
    weights: Mapping[str, Any],
    scores: Sequence[Any],
    provider_ids: set[str],
) -> None:
    criterion_ids = _require_unique_ids(criteria, "criteria")
    if set(weights) != criterion_ids:
        _fail("weights must match documented criteria exactly")
    if any(not isinstance(value, int) or isinstance(value, bool) for value in weights.values()):
        _fail("weights must be integer percentages")
    if sum(weights.values()) != EXPECTED_WEIGHT_TOTAL:
        _fail("weights must sum to 100")

    scored_provider_ids: set[str] = set()
    for score_index, raw_score in enumerate(scores):
        score = _as_mapping(raw_score, f"scores[{score_index}]")
        provider_id = score.get("provider")
        if provider_id not in provider_ids:
            _fail(f"score references unknown provider: {provider_id}")
        if provider_id in scored_provider_ids:
            _fail(f"duplicate score for provider: {provider_id}")
        scored_provider_ids.add(provider_id)

        category_scores = _as_mapping(
            score.get("category_scores"),
            f"scores[{score_index}].category_scores",
        )
        if set(category_scores) != criterion_ids:
            _fail(f"category scores incomplete for provider: {provider_id}")
        calculated = Decimal(0)
        for criterion_id, raw_value in category_scores.items():
            value = _parse_decimal(
                raw_value,
                f"scores[{score_index}].category_scores.{criterion_id}",
            )
            if value < 0 or value > 5:
                _fail(f"category score out of bounds: {provider_id}.{criterion_id}")
            calculated += value / Decimal(5) * Decimal(weights[criterion_id])

        raw_weighted = _parse_decimal(
            score.get("raw_weighted_score"),
            f"scores[{score_index}].raw_weighted_score",
        )
        calculated = calculated.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)
        if calculated != raw_weighted:
            _fail(
                f"raw score is not reproducible for {provider_id}: "
                f"expected {calculated}, got {raw_weighted}"
            )
        confidence = _parse_decimal(
            score.get("confidence_factor"),
            f"scores[{score_index}].confidence_factor",
        )
        if confidence < 0 or confidence > 1:
            _fail(f"confidence factor out of bounds: {provider_id}")
        adjusted = _parse_decimal(
            score.get("weighted_after_confidence"),
            f"scores[{score_index}].weighted_after_confidence",
        )
        expected_adjusted = (calculated * confidence).quantize(
            SCORE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        if expected_adjusted != adjusted:
            _fail(f"confidence-adjusted score is not reproducible for {provider_id}")
        penalty = _parse_decimal(
            score.get("unresolved_risk_penalty"),
            f"scores[{score_index}].unresolved_risk_penalty",
        )
        final = _parse_decimal(
            score.get("final_score"),
            f"scores[{score_index}].final_score",
        )
        if adjusted - penalty != final:
            _fail(f"final score is not reproducible for {provider_id}")

    if scored_provider_ids != provider_ids:
        _fail("every evaluated provider must have exactly one score")


def _validate_recommendation(
    recommendation: Mapping[str, Any],
    provider_ids: set[str],
) -> None:
    if recommendation.get("decision") != "no_provider_selected_yet":
        _fail("this dated artifact records a deferred provider selection")
    for field in ("primary_candidate", "secondary_candidate"):
        if recommendation.get(field) not in provider_ids:
            _fail(f"recommendation.{field} must reference an evaluated provider")
    order = _as_sequence(recommendation.get("evaluation_order"), "recommendation.evaluation_order")
    if set(order) != provider_ids or len(order) != len(provider_ids):
        _fail("evaluation order must contain every provider exactly once")


def _validate_unknown_pricing(providers: Sequence[Any]) -> None:
    azure = next(
        (
            _as_mapping(provider, "provider")
            for provider in providers
            if _as_mapping(provider, "provider").get("id") == "azure"
        ),
        None,
    )
    if azure is None:
        _fail("Azure evaluation is required")
    pricing = _as_sequence(azure.get("pricing"), "azure.pricing")
    if not pricing or any(
        _as_mapping(item, "azure.pricing item").get("price") is not None for item in pricing
    ):
        _fail("unverified Azure numeric pricing must remain null")


def _scan_sensitive(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = key.lower()
            if any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS):
                _fail(f"sensitive key forbidden at {path}.{key}")
            _scan_sensitive(child, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _scan_sensitive(child, f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in SENSITIVE_VALUE_PATTERNS:
            if pattern.search(value):
                _fail(f"sensitive value forbidden at {path}")


def validate_artifact(path: Path) -> dict[str, Any]:
    """Validate the artifact and return the parsed document."""

    document = load_strict_json(path)
    if document.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        _fail("unsupported schema_version")

    researched_at = _parse_utc_datetime(document.get("researched_at"), "researched_at")
    review_after = _parse_date(document.get("review_after"), "review_after")
    if review_after <= researched_at.date():
        _fail("review_after must be later than researched_at")

    sources = _as_sequence(document.get("sources"), "sources")
    source_ids = _validate_sources(sources)
    providers = _as_sequence(document.get("providers"), "providers")
    provider_ids = _validate_material_findings(providers, source_ids)
    _validate_unknown_pricing(providers)

    criteria = _as_sequence(document.get("criteria"), "criteria")
    weights = _as_mapping(document.get("weights"), "weights")
    scores = _as_sequence(document.get("scores"), "scores")
    _validate_scores(criteria, weights, scores, provider_ids)
    recommendation = _as_mapping(document.get("recommendation"), "recommendation")
    _validate_recommendation(recommendation, provider_ids)
    _scan_sensitive(document)

    expected = (
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if path.read_text(encoding="utf-8") != expected:
        _fail("artifact is not canonical sorted JSON with a trailing newline")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("docs/research/tts-provider-evaluation.json"),
    )
    args = parser.parse_args()
    document = validate_artifact(args.path)
    print(
        "valid TTS provider research artifact: "
        f"{len(document['providers'])} providers, {len(document['sources'])} sources"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
