"""Pure offline helpers for the ORION TTS listening-test preparation package."""

from __future__ import annotations

import hashlib
import hmac
import json
import statistics
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

CATEGORY_WEIGHTS = {
    "artifacts": Decimal("10"),
    "cross_segment_consistency": Decimal("15"),
    "latin_american_neutrality": Decimal("20"),
    "naturalness": Decimal("25"),
    "pronunciation": Decimal("20"),
    "prosody_and_pacing": Decimal("10"),
}
SCORE_QUANTUM = Decimal("0.1")
HARD_DISQUALIFICATION_FLAGS = frozenset(
    {
        "ambiguous_billable_submission",
        "missing_audio",
        "output_corruption",
        "provider_returned_different_text",
        "region_or_voice_unavailable",
        "terms_privacy_mismatch",
        "unexpected_charge",
        "unsafe_content_substitution",
    }
)
TWO_REPORT_DISQUALIFICATION_FLAGS = frozenset(
    {
        "critical_date_error",
        "critical_number_or_currency_error",
        "repeated_truncation",
        "unintelligible_segment",
    }
)
THREE_REPORT_DISQUALIFICATION_FLAGS = frozenset(
    {
        "critical_colombian_place_name_error",
        "serious_voice_drift",
    }
)
ALLOWED_CRITICAL_FAILURE_FLAGS = (
    HARD_DISQUALIFICATION_FLAGS
    | TWO_REPORT_DISQUALIFICATION_FLAGS
    | THREE_REPORT_DISQUALIFICATION_FLAGS
    | {
        "abbreviation_error",
        "clipping_or_encoding_artifact",
        "unnatural_pause",
        "voice_drift",
    }
)


class ListeningTestSupportError(ValueError):
    """Raised when offline test data violates the fixed protocol."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return the canonical research JSON representation used for hashing."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def compact_canonical_json_bytes(value: Any) -> bytes:
    """Return a compact canonical representation for derived identities."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_test_text(text: str) -> str:
    """Normalize public Spanish test text without altering meaningful punctuation."""

    if not isinstance(text, str):
        raise ListeningTestSupportError("listening-test text must be a string")
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    if "\n" in normalized:
        raise ListeningTestSupportError("listening-test samples must be single-line text")
    if any(character.isspace() and character != " " for character in normalized):
        raise ListeningTestSupportError("listening-test text contains unsupported whitespace")
    normalized = " ".join(part for part in normalized.strip().split(" ") if part)
    if not normalized:
        raise ListeningTestSupportError("listening-test text must not be empty")
    return normalized


def normalized_text_facts(text: str) -> dict[str, int | str]:
    normalized = normalize_test_text(text)
    encoded = normalized.encode("utf-8")
    return {
        "normalized_character_count": len(normalized),
        "normalized_text_hash": sha256_hex(encoded),
        "normalized_utf8_byte_count": len(encoded),
    }


def script_version_hash(samples: Sequence[Mapping[str, Any]]) -> str:
    identity = [
        {
            "normalized_character_count": sample["normalized_character_count"],
            "normalized_text_hash": sample["normalized_text_hash"],
            "normalized_utf8_byte_count": sample["normalized_utf8_byte_count"],
            "sample_id": sample["sample_id"],
        }
        for sample in samples
    ]
    return sha256_hex(compact_canonical_json_bytes(identity))


def generation_unit_id(
    *,
    candidate_id: str,
    candidate_snapshot_hash: str,
    research_snapshot_hash: str,
    sample_id: str,
    script_snapshot_hash: str,
) -> str:
    identity = {
        "candidate_id": candidate_id,
        "candidate_snapshot_hash": candidate_snapshot_hash,
        "research_snapshot_hash": research_snapshot_hash,
        "sample_id": sample_id,
        "script_snapshot_hash": script_snapshot_hash,
    }
    return f"gu-{sha256_hex(compact_canonical_json_bytes(identity))[:32]}"


def build_generation_plan_document(
    *,
    candidate_document: Mapping[str, Any],
    candidate_snapshot_hash: str,
    created_at: str,
    research_snapshot_hash: str,
    script_document: Mapping[str, Any],
    script_snapshot_hash: str,
) -> dict[str, Any]:
    """Expand candidate × sample into blocked, text-free preparation records."""

    units: list[dict[str, Any]] = []
    for candidate in candidate_document["candidates"]:
        for sample in script_document["samples"]:
            units.append(
                {
                    "authorization_status": "not_authorized",
                    "candidate_id": candidate["candidate_id"],
                    "candidate_snapshot_hash": candidate_snapshot_hash,
                    "currency": candidate["currency"],
                    "estimated_maximum_cost": None,
                    "estimated_minimum_cost": None,
                    "execution_status": "blocked",
                    "future_request_fingerprint_inputs": {
                        "candidate_id": candidate["candidate_id"],
                        "generation_mode": candidate["provider_generation_mode"],
                        "language": candidate["language"],
                        "locale": candidate["locale"],
                        "normalized_text_hash": sample["normalized_text_hash"],
                        "provider_model": candidate["provider_model"],
                        "provider_voice": candidate["provider_voice"],
                        "requested_output_format": candidate["requested_output_format"],
                        "sample_id": sample["sample_id"],
                        "target_channel_count": candidate["target_channel_count"],
                        "target_sample_rate_hz": candidate["target_sample_rate_hz"],
                    },
                    "generation_unit_id": generation_unit_id(
                        candidate_id=candidate["candidate_id"],
                        candidate_snapshot_hash=candidate_snapshot_hash,
                        research_snapshot_hash=research_snapshot_hash,
                        sample_id=sample["sample_id"],
                        script_snapshot_hash=script_snapshot_hash,
                    ),
                    "maximum_authorized_cost": None,
                    "normalized_text_hash": sample["normalized_text_hash"],
                    "output_status": "absent",
                    "pricing_status": candidate["pricing_status"],
                    "research_snapshot_hash": research_snapshot_hash,
                    "safe_notes": ["Blocked preparation record; no provider request body exists."],
                    "sample_id": sample["sample_id"],
                    "script_snapshot_hash": script_snapshot_hash,
                }
            )
    return {
        "candidate_snapshot_hash": candidate_snapshot_hash,
        "created_at": created_at,
        "generation_units": units,
        "policy_budget": {
            "account_balance_authorizes_execution": False,
            "currency": "USD",
            "current_authorized_amount": None,
            "free_credit_authorizes_execution": False,
            "policy_maximum": "10.00",
            "subscription_allowance_authorizes_execution": False,
        },
        "research_snapshot_hash": research_snapshot_hash,
        "safe_metadata": {
            "audio_generated": False,
            "billable_request_authorized": False,
            "provider_request_possible": False,
            "test_executed": False,
        },
        "schema_version": "1.0.0",
        "script_artifact": "docs/research/tts-listening-test-script.es.json",
        "script_snapshot_hash": script_snapshot_hash,
        "script_version_hash": script_document["script_version_hash"],
    }


def test_plan_hash(
    *,
    candidate_snapshot_hash: str,
    generation_plan_hash: str,
    protocol_version: str,
    research_snapshot_hash: str,
    script_snapshot_hash: str,
) -> str:
    identity = {
        "candidate_snapshot_hash": candidate_snapshot_hash,
        "generation_plan_hash": generation_plan_hash,
        "protocol_version": protocol_version,
        "research_snapshot_hash": research_snapshot_hash,
        "script_snapshot_hash": script_snapshot_hash,
    }
    return sha256_hex(compact_canonical_json_bytes(identity))


def _validate_blind_key(secret: bytes) -> None:
    if len(secret) < 32:
        raise ListeningTestSupportError("blind HMAC key must contain at least 32 bytes")
    lowered = secret.lower()
    if lowered in {b"default", b"change-me", b"secret"} or len(set(secret)) == 1:
        raise ListeningTestSupportError("blind HMAC key is empty, default, or structurally unsafe")


def blind_sample_id(
    *,
    candidate_id: str,
    evaluator_id: str,
    run_id: str,
    sample_id: str,
    secret: bytes,
) -> str:
    """Derive an opaque evaluator-specific ID without persisting the supplied key."""

    _validate_blind_key(secret)
    payload = compact_canonical_json_bytes(
        {
            "candidate_id": candidate_id,
            "domain": "orion-tts-listening-blind-id-v1",
            "evaluator_id": evaluator_id,
            "run_id": run_id,
            "sample_id": sample_id,
        }
    )
    return f"bs-{hmac.new(secret, payload, hashlib.sha256).hexdigest()[:24]}"


def evaluator_blind_order(
    *,
    candidate_ids: Sequence[str],
    evaluator_id: str,
    run_id: str,
    sample_ids: Sequence[str],
    secret: bytes,
) -> tuple[str, ...]:
    """Return a deterministic evaluator-specific order of opaque IDs."""

    _validate_blind_key(secret)
    items: list[tuple[str, str]] = []
    for candidate_id in candidate_ids:
        for sample_id in sample_ids:
            blind_id = blind_sample_id(
                candidate_id=candidate_id,
                evaluator_id=evaluator_id,
                run_id=run_id,
                sample_id=sample_id,
                secret=secret,
            )
            order_payload = compact_canonical_json_bytes(
                {
                    "blind_sample_id": blind_id,
                    "domain": "orion-tts-listening-order-v1",
                    "evaluator_id": evaluator_id,
                    "run_id": run_id,
                }
            )
            order_key = hmac.new(secret, order_payload, hashlib.sha256).hexdigest()
            items.append((order_key, blind_id))
    ordered = tuple(blind_id for _, blind_id in sorted(items))
    if len(ordered) != len(set(ordered)):
        raise ListeningTestSupportError("blind sample ID collision")
    return ordered


def _decimal_score(value: Any, *, path: str) -> Decimal:
    if isinstance(value, float):
        raise ListeningTestSupportError(f"{path} must not use float")
    try:
        score = Decimal(str(value))
    except InvalidOperation as exc:
        raise ListeningTestSupportError(f"{path} must be Decimal-compatible") from exc
    if score < 1 or score > 5:
        raise ListeningTestSupportError(f"{path} must be within 1-5")
    return score


def _median(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ListeningTestSupportError("cannot calculate a median without values")
    return Decimal(statistics.median(values))


def _quartiles(values: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0], ordered[0]
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        lower = ordered[:midpoint]
        upper = ordered[midpoint + 1 :]
    else:
        lower = ordered[:midpoint]
        upper = ordered[midpoint:]
    return _median(lower), _median(upper)


def _weighted_score(category_scores: Mapping[str, Decimal]) -> Decimal:
    value = sum(
        category_scores[category] * weight / Decimal("100")
        for category, weight in CATEGORY_WEIGHTS.items()
    )
    return value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def aggregate_scorecards(
    *,
    blind_mapping: Mapping[str, Mapping[str, str]],
    normalization_specification_hash: str,
    required_sample_ids: Sequence[str],
    scorecards: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Produce descriptive blind results without making a provider-selection decision."""

    if len(scorecards) < 5:
        raise ListeningTestSupportError("at least five evaluator scorecards are required")
    evaluator_metadata = [
        scorecard.get("evaluator_metadata")
        if isinstance(scorecard.get("evaluator_metadata"), Mapping)
        else {}
        for scorecard in scorecards
    ]
    evaluator_ids = [metadata.get("evaluator_id") for metadata in evaluator_metadata]
    if any(not isinstance(value, str) or not value for value in evaluator_ids):
        raise ListeningTestSupportError("every scorecard needs a pseudonymous evaluator ID")
    if len(evaluator_ids) != len(set(evaluator_ids)):
        raise ListeningTestSupportError("duplicate evaluator submission")
    scorecard_ids = [scorecard.get("scorecard_id") for scorecard in scorecards]
    if any(not isinstance(value, str) or not value for value in scorecard_ids):
        raise ListeningTestSupportError("every submission needs a scorecard ID")
    if len(scorecard_ids) != len(set(scorecard_ids)):
        raise ListeningTestSupportError("duplicate scorecard submission")
    strong_count = sum(
        metadata.get("colombian_spanish_familiarity") == "strong" for metadata in evaluator_metadata
    )
    if strong_count < 3:
        raise ListeningTestSupportError(
            "at least three evaluators need strong Colombian-Spanish familiarity"
        )

    if not blind_mapping:
        raise ListeningTestSupportError("blind mapping must not be empty")
    sample_ids = set(required_sample_ids)
    mapped_samples = {identity.get("sample_id") for identity in blind_mapping.values()}
    if mapped_samples != sample_ids:
        raise ListeningTestSupportError("blind mapping does not cover required samples")

    category_values: dict[str, dict[str, list[Decimal]]] = defaultdict(lambda: defaultdict(list))
    weighted_values: dict[str, list[Decimal]] = defaultdict(list)
    sample_weighted_values: dict[str, dict[str, list[Decimal]]] = defaultdict(
        lambda: defaultdict(list)
    )
    failure_counts: dict[str, Counter[str]] = defaultdict(Counter)
    failure_by_output: Counter[tuple[str, str, str]] = Counter()
    forced_choice_wins: Counter[str] = Counter()

    for scorecard, metadata in zip(scorecards, evaluator_metadata, strict=True):
        evaluator_id = str(metadata["evaluator_id"])
        if metadata.get("spanish_fluency_category") != "fluent_latin_american":
            raise ListeningTestSupportError("evaluator is not in the required fluency category")
        if metadata.get("colombian_spanish_familiarity") not in {
            "none",
            "general",
            "strong",
        }:
            raise ListeningTestSupportError("Colombian-Spanish familiarity category is invalid")
        if metadata.get("audio_playback_device_category") not in {
            "earbuds",
            "headphones",
            "other",
            "speakers",
        }:
            raise ListeningTestSupportError("audio playback device category is invalid")
        if scorecard.get("status") != "completed" or scorecard.get("completion_timestamp") is None:
            raise ListeningTestSupportError("scorecard is not completed")
        consent = scorecard.get("consent")
        if not isinstance(consent, Mapping) or any(value is not True for value in consent.values()):
            raise ListeningTestSupportError("scorecard consent confirmations are incomplete")
        evaluator_required_blind_ids = {
            blind_id
            for blind_id, identity in blind_mapping.items()
            if identity.get("evaluator_id") in {None, evaluator_id}
        }
        if not evaluator_required_blind_ids:
            raise ListeningTestSupportError("evaluator has no blind package mapping")
        if scorecard.get("normalization_specification_hash") != normalization_specification_hash:
            raise ListeningTestSupportError("normalization differs across evaluator packages")
        raw_scores = scorecard.get("blind_sample_scores")
        if not isinstance(raw_scores, Sequence) or isinstance(raw_scores, (str, bytes)):
            raise ListeningTestSupportError("scorecard scores must be an array")
        seen_blind_ids: set[str] = set()
        for score_index, raw_score in enumerate(raw_scores):
            if not isinstance(raw_score, Mapping):
                raise ListeningTestSupportError("score entry must be an object")
            blind_id = raw_score.get("blind_sample_id")
            if blind_id not in blind_mapping:
                raise ListeningTestSupportError("scorecard references an unknown blind ID")
            if blind_id in seen_blind_ids:
                raise ListeningTestSupportError("scorecard contains a duplicate blind ID")
            seen_blind_ids.add(str(blind_id))
            identity = blind_mapping[str(blind_id)]
            candidate_id = identity["candidate_id"]
            sample_id = identity["sample_id"]
            raw_categories = raw_score.get("category_scores")
            if not isinstance(raw_categories, Mapping):
                raise ListeningTestSupportError("category scores must be an object")
            if set(raw_categories) != set(CATEGORY_WEIGHTS):
                raise ListeningTestSupportError("category scores are incomplete")
            categories = {
                category: _decimal_score(
                    raw_categories[category],
                    path=f"scores[{score_index}].{category}",
                )
                for category in CATEGORY_WEIGHTS
            }
            weighted = _weighted_score(categories)
            weighted_values[candidate_id].append(weighted)
            sample_weighted_values[candidate_id][sample_id].append(weighted)
            for category, value in categories.items():
                category_values[candidate_id][category].append(value)

            raw_failures = raw_score.get("critical_failures", [])
            if not isinstance(raw_failures, Sequence) or isinstance(
                raw_failures,
                (str, bytes),
            ):
                raise ListeningTestSupportError("critical failures must be an array")
            failures = [str(value) for value in raw_failures]
            if len(failures) != len(set(failures)):
                raise ListeningTestSupportError("critical failure flags must be unique")
            if not set(failures).issubset(ALLOWED_CRITICAL_FAILURE_FLAGS):
                raise ListeningTestSupportError("scorecard contains an unknown failure flag")
            if raw_score.get("evaluator_confidence") not in {"low", "medium", "high"}:
                raise ListeningTestSupportError("evaluator confidence is invalid")
            comment = raw_score.get("optional_safe_comment")
            if comment is not None:
                if not isinstance(comment, str) or len(comment) > 500:
                    raise ListeningTestSupportError("optional evaluator comment is invalid")
                lowered = comment.casefold()
                if (
                    "http://" in lowered
                    or "https://" in lowered
                    or "bearer " in lowered
                    or any(ord(character) < 32 for character in comment)
                ):
                    raise ListeningTestSupportError("optional evaluator comment is unsafe")
            for failure in failures:
                failure_counts[candidate_id][failure] += 1
                failure_by_output[(candidate_id, sample_id, failure)] += 1
        if seen_blind_ids != evaluator_required_blind_ids:
            raise ListeningTestSupportError("scorecard is missing required blind samples")

        raw_choices = scorecard.get("forced_choices")
        if not isinstance(raw_choices, Sequence) or isinstance(raw_choices, (str, bytes)):
            raise ListeningTestSupportError("forced choices must be an array")
        seen_choice_samples: set[str] = set()
        for raw_choice in raw_choices:
            if not isinstance(raw_choice, Mapping):
                raise ListeningTestSupportError("forced choice must be an object")
            sample_id = raw_choice.get("sample_id")
            blind_id = raw_choice.get("blind_sample_id")
            if sample_id not in sample_ids or sample_id in seen_choice_samples:
                raise ListeningTestSupportError("forced-choice sample is invalid or duplicated")
            if blind_id not in blind_mapping:
                raise ListeningTestSupportError("forced choice references an unknown blind ID")
            if blind_mapping[str(blind_id)]["sample_id"] != sample_id:
                raise ListeningTestSupportError("forced choice does not match its sample")
            seen_choice_samples.add(str(sample_id))
            forced_choice_wins[blind_mapping[str(blind_id)]["candidate_id"]] += 1
        if seen_choice_samples != sample_ids:
            raise ListeningTestSupportError("forced choices are incomplete")

    candidates: dict[str, Any] = {}
    for candidate_id in sorted(weighted_values):
        category_aggregates: dict[str, Any] = {}
        for category in CATEGORY_WEIGHTS:
            values = category_values[candidate_id][category]
            category_aggregates[category] = {
                "mean": str(
                    (sum(values) / Decimal(len(values))).quantize(
                        SCORE_QUANTUM,
                        rounding=ROUND_HALF_UP,
                    )
                ),
                "median": str(_median(values).quantize(SCORE_QUANTUM)),
            }
        weighted = weighted_values[candidate_id]
        first_quartile, third_quartile = _quartiles(weighted)
        disqualification_reasons: set[str] = set()
        for failure in HARD_DISQUALIFICATION_FLAGS:
            if failure_counts[candidate_id][failure]:
                disqualification_reasons.add(failure)
        for (failure_candidate, _, failure), count in failure_by_output.items():
            if failure_candidate != candidate_id:
                continue
            if failure in TWO_REPORT_DISQUALIFICATION_FLAGS and count >= 2:
                disqualification_reasons.add(failure)
            if failure in THREE_REPORT_DISQUALIFICATION_FLAGS and count >= 3:
                disqualification_reasons.add(failure)
        candidates[candidate_id] = {
            "category_aggregates": category_aggregates,
            "critical_failure_counts": dict(sorted(failure_counts[candidate_id].items())),
            "disqualification_reasons": sorted(disqualification_reasons),
            "disqualified": bool(disqualification_reasons),
            "forced_choice_wins": forced_choice_wins[candidate_id],
            "mean_weighted_score": str(
                (sum(weighted) / Decimal(len(weighted))).quantize(
                    SCORE_QUANTUM,
                    rounding=ROUND_HALF_UP,
                )
            ),
            "median_weighted_score": str(
                _median(weighted).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)
            ),
            "per_sample_medians": {
                sample_id: str(_median(values).quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP))
                for sample_id, values in sorted(sample_weighted_values[candidate_id].items())
            },
            "weighted_score_iqr": str(
                (third_quartile - first_quartile).quantize(
                    SCORE_QUANTUM,
                    rounding=ROUND_HALF_UP,
                )
            ),
        }

    return {
        "candidate_descriptive_results": candidates,
        "completed_evaluator_count": len(scorecards),
        "recommendation_status": (
            "descriptive_results_only_with_disqualifications"
            if any(value["disqualified"] for value in candidates.values())
            else "descriptive_results_only"
        ),
        "strong_colombian_familiarity_count": strong_count,
    }
