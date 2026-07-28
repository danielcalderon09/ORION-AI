"""Validate ORION's offline, non-authorized TTS listening-test package."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts.tts_listening_test_support import (
        build_generation_plan_document,
        canonical_json_bytes,
        compact_canonical_json_bytes,
        generation_unit_id,
        normalized_text_facts,
        script_version_hash,
        sha256_hex,
        test_plan_hash,
    )
    from scripts.validate_tts_provider_evaluation import (
        validate_artifact as validate_provider_research,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from tts_listening_test_support import (  # type: ignore[no-redef]
        build_generation_plan_document,
        canonical_json_bytes,
        compact_canonical_json_bytes,
        generation_unit_id,
        normalized_text_facts,
        script_version_hash,
        sha256_hex,
        test_plan_hash,
    )
    from validate_tts_provider_evaluation import (  # type: ignore[no-redef]
        validate_artifact as validate_provider_research,
    )

SUPPORTED_SCHEMA_VERSIONS = {"1.0.0"}
EXPECTED_PROVIDER_IDS = {"aws_polly", "azure", "google"}
EXPECTED_EXCLUDED_PROVIDER_IDS = {"elevenlabs", "openai"}
EXPECTED_SAMPLE_CATEGORIES = {
    "abbreviations_technical_terms",
    "calm_expressive_closing",
    "colombian_place_names",
    "foreign_company_names",
    "neutral_narration",
    "numbers_dates_currency",
    "punctuation_pauses",
    "scene_to_scene_continuity",
}
POLICY_CURRENCY = "USD"
POLICY_MAXIMUM = Decimal("10.00")
FORBIDDEN_EXACT_KEYS = {
    "access_token",
    "account_id",
    "api_key",
    "authorization_header",
    "bearer",
    "blind_decoding_map",
    "bucket_name",
    "cloud_project_id",
    "cookie",
    "credential",
    "decoding_map",
    "endpoint",
    "hmac_key",
    "hmac_seed",
    "password",
    "provider_request_body",
    "provider_url",
    "request_body",
    "secret_key",
    "signed_url",
    "storage_bucket",
    "subscription_id",
}
FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"[?&](?:signature|sig|token|key)=", re.IGNORECASE),
)
PUBLIC_TEXT_SENSITIVE_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:\+?\d[\s().-]*){10,}\b"),
    re.compile(r"\b\d{6,}-\d{4,}\b"),
)


class ListeningTestPlanValidationError(ValueError):
    """Raised when the committed preparation package is unsafe or inconsistent."""


def _fail(message: str) -> NoReturn:
    raise ListeningTestPlanValidationError(message)


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


def load_canonical_json(path: Path) -> dict[str, Any]:
    """Load strict UTF-8 JSON and verify the repository's canonical representation."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ListeningTestPlanValidationError(f"missing artifact: {path}") from exc
    if len(raw) > 5_000_000:
        _fail(f"artifact exceeds the offline size limit: {path}")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail(f"artifact must not contain a UTF-8 BOM: {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ListeningTestPlanValidationError(f"artifact must be strict UTF-8: {path}") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except json.JSONDecodeError as exc:
        raise ListeningTestPlanValidationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        _fail(f"artifact root must be an object: {path}")
    if raw != canonical_json_bytes(value):
        _fail(f"artifact is not canonical sorted JSON with one trailing newline: {path}")
    return value


def _as_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{path} must be an object")
    return value


def _as_sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _fail(f"{path} must be an array")
    return value


def _parse_aware_datetime(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        _fail(f"{path} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ListeningTestPlanValidationError(f"{path} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(f"{path} must be timezone-aware")
    return parsed


def _parse_date(value: Any, path: str) -> date:
    if not isinstance(value, str):
        _fail(f"{path} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ListeningTestPlanValidationError(f"{path} must be an ISO date") from exc


def _parse_decimal(value: Any, path: str) -> Decimal:
    if not isinstance(value, str):
        _fail(f"{path} must be a Decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ListeningTestPlanValidationError(f"{path} is not Decimal-safe") from exc
    if not result.is_finite():
        _fail(f"{path} must be finite")
    return result


def _require_schema(document: Mapping[str, Any], path: str) -> None:
    if document.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        _fail(f"{path} has an unsupported schema_version")


def _require_unique_ids(
    items: Sequence[Any],
    *,
    field: str,
    path: str,
) -> set[str]:
    seen: set[str] = set()
    for index, raw_item in enumerate(items):
        item = _as_mapping(raw_item, f"{path}[{index}]")
        item_id = item.get(field)
        if not isinstance(item_id, str) or not item_id:
            _fail(f"{path}[{index}].{field} must be non-empty")
        if item_id in seen:
            _fail(f"duplicate {path} identity: {item_id}")
        seen.add(item_id)
    return seen


def scan_forbidden_content(value: Any, path: str = "$") -> None:
    """Reject secrets, endpoints, cloud identities, and live request material."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if key.casefold() in FORBIDDEN_EXACT_KEYS:
                _fail(f"forbidden sensitive key at {path}.{key}")
            scan_forbidden_content(child, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            scan_forbidden_content(child, f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in FORBIDDEN_VALUE_PATTERNS:
            if pattern.search(value):
                _fail(f"forbidden sensitive or endpoint-like value at {path}")


def validate_candidate_document(
    document: Mapping[str, Any],
    *,
    as_of: date,
    research_document: Mapping[str, Any],
    research_snapshot_hash: str,
) -> None:
    _require_schema(document, "candidate artifact")
    _parse_aware_datetime(document.get("created_at"), "candidate.created_at")
    review_after = _parse_date(document.get("review_after"), "candidate.review_after")
    if review_after < as_of:
        _fail("candidate research snapshot is stale")
    if document.get("research_snapshot_hash") != research_snapshot_hash:
        _fail("candidate research snapshot hash differs")

    research_providers = {
        provider["id"]: provider
        for provider in _as_sequence(research_document.get("providers"), "research.providers")
    }
    research_sources = {
        source["id"]: source
        for source in _as_sequence(research_document.get("sources"), "research.sources")
    }
    candidates = _as_sequence(document.get("candidates"), "candidates")
    candidate_ids = _require_unique_ids(candidates, field="candidate_id", path="candidates")
    provider_ids: set[str] = set()
    for index, raw_candidate in enumerate(candidates):
        candidate = _as_mapping(raw_candidate, f"candidates[{index}]")
        provider_id = candidate.get("provider_id")
        if provider_id not in research_providers:
            _fail(f"candidate references unknown provider: {provider_id}")
        if provider_id in provider_ids:
            _fail(f"candidate provider is duplicated: {provider_id}")
        provider_ids.add(str(provider_id))
        references = _as_sequence(candidate.get("source_ids"), f"candidates[{index}].source_ids")
        if not references:
            _fail("candidate must reference official evidence")
        for source_id in references:
            source = research_sources.get(source_id)
            if source is None:
                _fail(f"candidate references unknown source: {source_id}")
            if source.get("provider") != provider_id:
                _fail(f"candidate source belongs to another provider: {source_id}")
        if candidate.get("eligibility_status") != "blocked":
            _fail("committed candidate must remain blocked")
        if candidate.get("provider_model") is not None:
            _fail("unselected candidate model must remain null")
        if candidate.get("provider_voice") is not None:
            _fail("unselected candidate voice must remain null")
        if candidate.get("region") is not None:
            _fail("unverified candidate region must remain null")
        if candidate.get("maximum_estimated_cost") is not None:
            _fail("unverified candidate estimate must remain null")
        if candidate.get("maximum_authorized_cost") is not None:
            _fail("committed candidate authorization must remain null")
        if candidate.get("availability_status") != "unverified":
            _fail("candidate availability must remain unverified")
        if candidate.get("retention_review_status") != "pending":
            _fail("candidate retention review must remain pending")
        if candidate.get("commercial_review_status") != "pending":
            _fail("candidate commercial review must remain pending")
        if candidate.get("normalization_required") is not True:
            _fail("every candidate needs common listening normalization")
        blocking_reasons = set(
            _as_sequence(candidate.get("blocking_reasons"), "candidate.blocking_reasons")
        )
        required_blocks = {
            "candidate_model_unselected",
            "candidate_voice_unselected",
            "commercial_review_incomplete",
            "current_price_unverified",
            "regional_availability_unverified",
            "retention_review_incomplete",
            "separate_execution_authorization_absent",
        }
        if not required_blocks.issubset(blocking_reasons):
            _fail("candidate is missing mandatory blocking reasons")
        if (
            candidate.get("target_sample_rate_hz") != 24000
            or candidate.get("target_channel_count") != 1
            or candidate.get("target_sample_width_bytes") != 2
        ):
            _fail("candidate target audio format differs from the fixed comparison format")
        if candidate.get("timing_requested") is not False:
            _fail("timing must remain unrequested in the initial listening test")

    if provider_ids != EXPECTED_PROVIDER_IDS or len(candidate_ids) != 3:
        _fail("initial candidates must be exactly Azure, Google, and Amazon Polly")

    excluded = _as_sequence(document.get("excluded_providers"), "excluded_providers")
    excluded_ids = _require_unique_ids(excluded, field="provider_id", path="excluded_providers")
    if excluded_ids != EXPECTED_EXCLUDED_PROVIDER_IDS:
        _fail("excluded providers must be exactly OpenAI and ElevenLabs")
    if provider_ids & excluded_ids:
        _fail("an excluded provider is also a candidate")
    for index, raw_excluded in enumerate(excluded):
        excluded_provider = _as_mapping(raw_excluded, f"excluded_providers[{index}]")
        if excluded_provider.get("status") != "excluded_from_initial_test":
            _fail("excluded provider status is invalid")
        for source_id in _as_sequence(
            excluded_provider.get("source_ids"),
            "excluded_provider.source_ids",
        ):
            source = research_sources.get(source_id)
            if source is None or source.get("provider") != excluded_provider.get("provider_id"):
                _fail(f"excluded provider source is invalid: {source_id}")

    normalization = _as_mapping(
        document.get("normalization_specification"),
        "normalization_specification",
    )
    if (
        normalization.get("target_format") != "wav_pcm"
        or normalization.get("target_sample_rate_hz") != 24000
        or normalization.get("target_channel_count") != 1
        or normalization.get("target_sample_width_bytes") != 2
        or normalization.get("leading_silence_ms") != 250
        or normalization.get("trailing_silence_ms") != 250
        or normalization.get("retain_original_provider_output") is not True
    ):
        _fail("audio normalization specification differs from the fixed protocol")
    if _parse_decimal(normalization.get("loudness_target_lufs"), "loudness_target_lufs") != -16:
        _fail("listening loudness target must be -16 LUFS")
    if _parse_decimal(normalization.get("peak_ceiling_dbtp"), "peak_ceiling_dbtp") != -1:
        _fail("listening peak ceiling must be -1 dBTP")

    safe_metadata = _as_mapping(document.get("safe_metadata"), "candidate.safe_metadata")
    if any(safe_metadata.values()):
        _fail("candidate safe metadata must confirm no execution or selection")
    scan_forbidden_content(document)


def validate_script_document(document: Mapping[str, Any]) -> None:
    _require_schema(document, "script artifact")
    _parse_aware_datetime(document.get("created_at"), "script.created_at")
    samples = _as_sequence(document.get("samples"), "samples")
    sample_ids = _require_unique_ids(samples, field="sample_id", path="samples")
    if len(sample_ids) != 8:
        _fail("the versioned listening script must contain exactly eight samples")
    categories: set[str] = set()
    normalized_hashes: set[str] = set()
    for index, raw_sample in enumerate(samples):
        sample = _as_mapping(raw_sample, f"samples[{index}]")
        _require_schema(sample, f"samples[{index}]")
        category = sample.get("category")
        if category in categories:
            _fail(f"duplicate listening category: {category}")
        categories.add(str(category))
        text = sample.get("text")
        if not isinstance(text, str):
            _fail("sample text must be a string")
        facts = normalized_text_facts(text)
        if text != text.strip() or facts["normalized_text_hash"] != sample.get(
            "normalized_text_hash"
        ):
            _fail(f"sample text normalization or hash differs: {sample.get('sample_id')}")
        for field, value in facts.items():
            if sample.get(field) != value:
                _fail(f"sample text fact differs: {sample.get('sample_id')}.{field}")
        normalized_hash = str(facts["normalized_text_hash"])
        if normalized_hash in normalized_hashes:
            _fail("duplicate normalized listening sample")
        normalized_hashes.add(normalized_hash)
        critical_tokens = _as_sequence(
            sample.get("critical_tokens"),
            f"samples[{index}].critical_tokens",
        )
        if not critical_tokens or any(token not in text for token in critical_tokens):
            _fail(f"sample critical tokens are not preserved: {sample.get('sample_id')}")
        duration = _as_mapping(
            sample.get("expected_duration_range_ms"),
            "expected_duration_range_ms",
        )
        if (
            not isinstance(duration.get("minimum"), int)
            or not isinstance(duration.get("maximum"), int)
            or duration["minimum"] < 250
            or duration["minimum"] > duration["maximum"]
            or duration["maximum"] > 120000
        ):
            _fail("sample expected duration range is invalid")
        if (
            sample.get("allowed_for_external_submission") is not True
            or sample.get("privacy_classification") != "public_test_text"
        ):
            _fail("sample is not marked public-safe for external submission")
        if any(pattern.search(text) for pattern in PUBLIC_TEXT_SENSITIVE_PATTERNS):
            _fail(f"sample contains a personal-data indicator: {sample.get('sample_id')}")
    if categories != EXPECTED_SAMPLE_CATEGORIES:
        _fail("listening sample categories differ from the fixed protocol")
    if document.get("script_version_hash") != script_version_hash(samples):
        _fail("aggregate script-version hash differs")
    scan_forbidden_content({key: value for key, value in document.items() if key != "samples"})


def validate_generation_plan_document(
    document: Mapping[str, Any],
    *,
    candidate_document: Mapping[str, Any],
    candidate_snapshot_hash: str,
    research_snapshot_hash: str,
    script_document: Mapping[str, Any],
    script_snapshot_hash: str,
) -> None:
    _require_schema(document, "generation plan")
    created_at = document.get("created_at")
    _parse_aware_datetime(created_at, "generation_plan.created_at")
    expected = build_generation_plan_document(
        candidate_document=candidate_document,
        candidate_snapshot_hash=candidate_snapshot_hash,
        created_at=str(created_at),
        research_snapshot_hash=research_snapshot_hash,
        script_document=script_document,
        script_snapshot_hash=script_snapshot_hash,
    )
    if document != expected:
        _fail("generation plan is not the deterministic candidate × sample expansion")
    units = _as_sequence(document.get("generation_units"), "generation_units")
    unit_ids = _require_unique_ids(units, field="generation_unit_id", path="generation_units")
    if len(unit_ids) != 24:
        _fail("generation plan must contain exactly 24 blocked units")
    matrix = {
        (unit["candidate_id"], unit["sample_id"]) for unit in units if isinstance(unit, Mapping)
    }
    expected_matrix = {
        (candidate["candidate_id"], sample["sample_id"])
        for candidate in candidate_document["candidates"]
        for sample in script_document["samples"]
    }
    if matrix != expected_matrix:
        _fail("generation matrix is incomplete")
    for unit in units:
        if (
            unit.get("authorization_status") != "not_authorized"
            or unit.get("execution_status") != "blocked"
            or unit.get("output_status") != "absent"
        ):
            _fail("every committed generation unit must be blocked and absent")
        if any(
            unit.get(field) is not None
            for field in (
                "estimated_minimum_cost",
                "estimated_maximum_cost",
                "maximum_authorized_cost",
            )
        ):
            _fail("committed generation costs and authorization must remain null")
        if "text" in unit:
            _fail("generation unit must not duplicate narration text")
        expected_id = generation_unit_id(
            candidate_id=unit["candidate_id"],
            candidate_snapshot_hash=candidate_snapshot_hash,
            research_snapshot_hash=research_snapshot_hash,
            sample_id=unit["sample_id"],
            script_snapshot_hash=script_snapshot_hash,
        )
        if unit.get("generation_unit_id") != expected_id:
            _fail("generation unit identity differs")
    budget = _as_mapping(document.get("policy_budget"), "policy_budget")
    if (
        budget.get("currency") != POLICY_CURRENCY
        or _parse_decimal(budget.get("policy_maximum"), "policy_budget.policy_maximum")
        != POLICY_MAXIMUM
        or budget.get("current_authorized_amount") is not None
        or budget.get("free_credit_authorizes_execution") is not False
        or budget.get("subscription_allowance_authorizes_execution") is not False
        or budget.get("account_balance_authorizes_execution") is not False
    ):
        _fail("generation budget is not the non-authorizing USD 10 policy")
    scan_forbidden_content(document)


def validate_authorization_template(
    document: Mapping[str, Any],
    *,
    candidate_snapshot_hash: str,
    generation_plan_hash: str,
    research_snapshot_hash: str,
    script_snapshot_hash: str,
    expected_test_plan_hash: str,
) -> None:
    _require_schema(document, "authorization template")
    expected_values = {
        "approval_reference": None,
        "authorized_at": None,
        "authorized_by_role": None,
        "authorized_candidate_ids": [],
        "candidate_snapshot_hash": candidate_snapshot_hash,
        "commercial_review_approved": False,
        "currency": POLICY_CURRENCY,
        "execution_environment": None,
        "expires_at": None,
        "generation_plan_hash": generation_plan_hash,
        "per_candidate_budget_ceiling": None,
        "per_generation_unit_ceiling": None,
        "pricing_verified_at": None,
        "regional_availability_verified": False,
        "research_snapshot_hash": research_snapshot_hash,
        "retention_review_approved": False,
        "script_snapshot_hash": script_snapshot_hash,
        "status": "draft",
        "test_plan_hash": expected_test_plan_hash,
        "total_budget_ceiling": None,
    }
    for field, expected in expected_values.items():
        if document.get(field) != expected:
            _fail(f"committed authorization template appears live: {field}")
    scan_forbidden_content(document)


def validate_future_budget(
    authorization: Mapping[str, Any],
    unit_costs: Sequence[Mapping[str, Any]],
) -> None:
    """Fail closed for a future uncommitted authorization and bounded fake costs."""

    if authorization.get("status") != "authorized":
        _fail("future listening execution is not authorized")
    if (
        not authorization.get("authorized_candidate_ids")
        or authorization.get("authorized_at") is None
        or authorization.get("expires_at") is None
        or authorization.get("pricing_verified_at") is None
        or authorization.get("approval_reference") is None
    ):
        _fail("future listening authorization is incomplete")
    for field in (
        "commercial_review_approved",
        "regional_availability_verified",
        "retention_review_approved",
    ):
        if authorization.get(field) is not True:
            _fail(f"future listening authorization lacks approval: {field}")
    if authorization.get("currency") != POLICY_CURRENCY:
        _fail("future listening authorization currency must remain USD")
    total_ceiling = _parse_decimal(
        authorization.get("total_budget_ceiling"),
        "authorization.total_budget_ceiling",
    )
    candidate_ceiling = _parse_decimal(
        authorization.get("per_candidate_budget_ceiling"),
        "authorization.per_candidate_budget_ceiling",
    )
    unit_ceiling = _parse_decimal(
        authorization.get("per_generation_unit_ceiling"),
        "authorization.per_generation_unit_ceiling",
    )
    if total_ceiling <= 0 or total_ceiling > POLICY_MAXIMUM:
        _fail("future authorization exceeds the USD 10 policy ceiling")
    totals: dict[str, Decimal] = defaultdict(Decimal)
    overall = Decimal(0)
    authorized_candidates = set(authorization["authorized_candidate_ids"])
    if not unit_costs:
        _fail("future authorization requires verified unit costs")
    for index, raw_cost in enumerate(unit_costs):
        cost = _as_mapping(raw_cost, f"unit_costs[{index}]")
        candidate_id = cost.get("candidate_id")
        if candidate_id not in authorized_candidates:
            _fail("unit cost belongs to an unauthorized candidate")
        if cost.get("pricing_status") != "verified":
            _fail("unknown or unverified price blocks authorization")
        if cost.get("currency") != POLICY_CURRENCY:
            _fail("unit cost currency differs from policy currency")
        maximum = _parse_decimal(
            cost.get("estimated_maximum_cost"),
            f"unit_costs[{index}].estimated_maximum_cost",
        )
        if maximum < 0 or maximum > unit_ceiling:
            _fail("unit cost exceeds its authorization ceiling")
        totals[str(candidate_id)] += maximum
        overall += maximum
    if any(total > candidate_ceiling for total in totals.values()):
        _fail("candidate worst-case cost exceeds its authorization ceiling")
    if overall > total_ceiling:
        _fail("total worst-case cost exceeds its authorization ceiling")


def _validate_scorecard_template(
    document: Mapping[str, Any],
    *,
    normalization_specification_hash: str,
    expected_test_plan_hash: str,
) -> None:
    _require_schema(document, "scorecard template")
    if (
        document.get("status") != "template"
        or document.get("blind_sample_scores") != []
        or document.get("forced_choices") != []
        or document.get("completion_timestamp") is not None
        or document.get("evaluator_package_id") is not None
        or document.get("scorecard_id") is not None
        or document.get("test_plan_hash") != expected_test_plan_hash
        or document.get("normalization_specification_hash") != normalization_specification_hash
    ):
        _fail("committed scorecard template contains live evaluation state")
    metadata = _as_mapping(document.get("evaluator_metadata"), "scorecard.evaluator_metadata")
    if any(value is not None for value in metadata.values()):
        _fail("committed scorecard template contains evaluator data")
    consent = _as_mapping(document.get("consent"), "scorecard.consent")
    if any(value is not False for value in consent.values()):
        _fail("committed scorecard consent must remain false")
    scan_forbidden_content(document)


def _validate_results_template(
    document: Mapping[str, Any],
    *,
    generation_plan_hash: str,
    research_snapshot_hash: str,
    script_snapshot_hash: str,
    expected_test_plan_hash: str,
) -> None:
    _require_schema(document, "results template")
    if (
        document.get("status") != "template"
        or document.get("test_run_id") is not None
        or document.get("candidate_blind_mapping_reference") is not None
        or document.get("candidate_results") != []
        or document.get("category_aggregates") != []
        or document.get("critical_failures") != []
        or document.get("disqualifications") != []
        or document.get("final_measured_score") is not None
        or document.get("forced_choice_wins") != []
        or document.get("mean_scores") != []
        or document.get("median_scores") != []
        or document.get("completed_evaluator_count") != 0
        or document.get("normalization_consistent") is not False
        or document.get("normalized_cost") is not None
        or document.get("recommendation_status") != "not_evaluated"
        or document.get("evidence_confidence") != "absent"
        or document.get("generation_plan_hash") != generation_plan_hash
        or document.get("research_snapshot_hash") != research_snapshot_hash
        or document.get("script_snapshot_hash") != script_snapshot_hash
        or document.get("spread_indicators") != []
        or document.get("test_plan_hash") != expected_test_plan_hash
    ):
        _fail("committed results template contains live evaluation state")
    scan_forbidden_content(document)


def validate_package(root: Path, *, as_of: date | None = None) -> dict[str, Any]:
    """Validate the complete linked preparation package without I/O beyond local reads."""

    research_path = root / "docs/research/tts-provider-evaluation.json"
    candidate_path = root / "docs/research/tts-listening-test-candidates.json"
    script_path = root / "docs/research/tts-listening-test-script.es.json"
    generation_path = root / "docs/research/tts-listening-test-generation-plan.json"
    authorization_path = root / "docs/research/tts-listening-test-authorization.template.json"
    scorecard_path = root / "docs/research/tts-listening-scorecard.template.json"
    results_path = root / "docs/research/tts-listening-results.template.json"

    research_document = validate_provider_research(research_path)
    research_snapshot_hash = sha256_hex(research_path.read_bytes())
    candidate_document = load_canonical_json(candidate_path)
    validate_candidate_document(
        candidate_document,
        as_of=as_of or date.today(),
        research_document=research_document,
        research_snapshot_hash=research_snapshot_hash,
    )
    candidate_snapshot_hash = sha256_hex(candidate_path.read_bytes())

    script_document = load_canonical_json(script_path)
    validate_script_document(script_document)
    script_snapshot_hash = sha256_hex(script_path.read_bytes())

    generation_document = load_canonical_json(generation_path)
    validate_generation_plan_document(
        generation_document,
        candidate_document=candidate_document,
        candidate_snapshot_hash=candidate_snapshot_hash,
        research_snapshot_hash=research_snapshot_hash,
        script_document=script_document,
        script_snapshot_hash=script_snapshot_hash,
    )
    generation_plan_hash = sha256_hex(generation_path.read_bytes())
    expected_test_plan_hash = test_plan_hash(
        candidate_snapshot_hash=candidate_snapshot_hash,
        generation_plan_hash=generation_plan_hash,
        protocol_version="1.0.0",
        research_snapshot_hash=research_snapshot_hash,
        script_snapshot_hash=script_snapshot_hash,
    )
    normalization_specification_hash = sha256_hex(
        compact_canonical_json_bytes(candidate_document["normalization_specification"])
    )

    authorization = load_canonical_json(authorization_path)
    validate_authorization_template(
        authorization,
        candidate_snapshot_hash=candidate_snapshot_hash,
        generation_plan_hash=generation_plan_hash,
        research_snapshot_hash=research_snapshot_hash,
        script_snapshot_hash=script_snapshot_hash,
        expected_test_plan_hash=expected_test_plan_hash,
    )
    scorecard = load_canonical_json(scorecard_path)
    _validate_scorecard_template(
        scorecard,
        normalization_specification_hash=normalization_specification_hash,
        expected_test_plan_hash=expected_test_plan_hash,
    )
    results = load_canonical_json(results_path)
    _validate_results_template(
        results,
        generation_plan_hash=generation_plan_hash,
        research_snapshot_hash=research_snapshot_hash,
        script_snapshot_hash=script_snapshot_hash,
        expected_test_plan_hash=expected_test_plan_hash,
    )

    return {
        "candidate_count": len(candidate_document["candidates"]),
        "candidate_snapshot_hash": candidate_snapshot_hash,
        "generation_plan_hash": generation_plan_hash,
        "generation_unit_count": len(generation_document["generation_units"]),
        "research_snapshot_hash": research_snapshot_hash,
        "sample_count": len(script_document["samples"]),
        "script_snapshot_hash": script_snapshot_hash,
        "test_plan_hash": expected_test_plan_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    result = validate_package(args.root)
    print(
        "valid blocked TTS listening-test package: "
        f"{result['candidate_count']} candidates, "
        f"{result['sample_count']} samples, "
        f"{result['generation_unit_count']} generation units"
    )
    print(f"research_snapshot_hash={result['research_snapshot_hash']}")
    print(f"candidate_snapshot_hash={result['candidate_snapshot_hash']}")
    print(f"script_snapshot_hash={result['script_snapshot_hash']}")
    print(f"generation_plan_hash={result['generation_plan_hash']}")
    print(f"test_plan_hash={result['test_plan_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
