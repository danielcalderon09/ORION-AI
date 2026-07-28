from __future__ import annotations

import ast
import copy
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts.tts_listening_test_support import (
    ListeningTestSupportError,
    aggregate_scorecards,
    blind_sample_id,
    evaluator_blind_order,
    normalize_test_text,
    normalized_text_facts,
)
from scripts.validate_tts_listening_test_plan import (
    ListeningTestPlanValidationError,
    load_canonical_json,
    scan_forbidden_content,
    validate_authorization_template,
    validate_candidate_document,
    validate_future_budget,
    validate_generation_plan_document,
    validate_package,
    validate_script_document,
)

ROOT = Path(__file__).resolve().parents[4]
RESEARCH_DIR = ROOT / "docs" / "research"
FAKE_BLIND_KEY = b"TEST-ONLY-fixture-key-not-for-production-0001"
NORMALIZATION_HASH = "9296d46cdf8ebdf0f3f6a2e62f62419621aa9bc3c03d06417f7bb0ada2a2650d"


def _artifact(name: str) -> dict[str, Any]:
    return load_canonical_json(RESEARCH_DIR / name)


def _copy_package(tmp_path: Path) -> Path:
    destination = tmp_path / "repository"
    research = destination / "docs" / "research"
    research.parent.mkdir(parents=True)
    shutil.copytree(RESEARCH_DIR, research)
    return destination


def _write_canonical(path: Path, value: dict[str, Any]) -> None:
    from scripts.tts_listening_test_support import canonical_json_bytes

    path.write_bytes(canonical_json_bytes(value))


def _candidate_validation_inputs() -> tuple[dict[str, Any], dict[str, Any], str]:
    candidate = _artifact("tts-listening-test-candidates.json")
    research = _artifact("tts-provider-evaluation.json")
    research_hash = candidate["research_snapshot_hash"]
    return candidate, research, research_hash


def _future_authorization() -> dict[str, Any]:
    return {
        "approval_reference": "TEST-ONLY-APPROVAL",
        "authorized_at": "2026-08-01T12:00:00Z",
        "authorized_candidate_ids": [
            "candidate-azure-01",
            "candidate-google-01",
            "candidate-polly-01",
        ],
        "commercial_review_approved": True,
        "currency": "USD",
        "expires_at": "2026-08-02T12:00:00Z",
        "per_candidate_budget_ceiling": "1.00",
        "per_generation_unit_ceiling": "0.20",
        "pricing_verified_at": "2026-08-01T11:00:00Z",
        "regional_availability_verified": True,
        "retention_review_approved": True,
        "status": "authorized",
        "total_budget_ceiling": "3.00",
    }


def _unit_costs() -> list[dict[str, str]]:
    return [
        {
            "candidate_id": candidate_id,
            "currency": "USD",
            "estimated_maximum_cost": "0.10",
            "pricing_status": "verified",
        }
        for candidate_id in (
            "candidate-azure-01",
            "candidate-google-01",
            "candidate-polly-01",
        )
        for _ in range(8)
    ]


def _scorecard_fixture(
    *,
    evaluator_count: int = 5,
    strong_count: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], list[str]]:
    candidate_ids = [
        "candidate-azure-01",
        "candidate-google-01",
        "candidate-polly-01",
    ]
    sample_ids = [
        sample["sample_id"] for sample in _artifact("tts-listening-test-script.es.json")["samples"]
    ]
    mapping: dict[str, dict[str, str]] = {}
    scorecards: list[dict[str, Any]] = []
    for evaluator_index in range(evaluator_count):
        evaluator_id = f"eval-{evaluator_index + 1:08d}"
        blind_scores: list[dict[str, Any]] = []
        forced_choices: list[dict[str, str]] = []
        per_sample: dict[str, list[str]] = {sample_id: [] for sample_id in sample_ids}
        for candidate_id in candidate_ids:
            for sample_id in sample_ids:
                blind_id = blind_sample_id(
                    candidate_id=candidate_id,
                    evaluator_id=evaluator_id,
                    run_id="test-run-fixture",
                    sample_id=sample_id,
                    secret=FAKE_BLIND_KEY,
                )
                mapping[blind_id] = {
                    "candidate_id": candidate_id,
                    "evaluator_id": evaluator_id,
                    "sample_id": sample_id,
                }
                per_sample[sample_id].append(blind_id)
                blind_scores.append(
                    {
                        "blind_sample_id": blind_id,
                        "category_scores": {
                            "artifacts": "4",
                            "cross_segment_consistency": "4",
                            "latin_american_neutrality": "4",
                            "naturalness": "4",
                            "pronunciation": "4",
                            "prosody_and_pacing": "4",
                        },
                        "critical_failures": [],
                        "evaluator_confidence": "high",
                        "optional_safe_comment": None,
                    }
                )
        for sample_id in sample_ids:
            forced_choices.append(
                {
                    "blind_sample_id": per_sample[sample_id][evaluator_index % 3],
                    "sample_id": sample_id,
                }
            )
        scorecards.append(
            {
                "blind_sample_scores": blind_scores,
                "completion_timestamp": f"2026-08-01T12:0{evaluator_index}:00Z",
                "consent": {
                    "evaluation_data_minimization_accepted": True,
                    "independent_scoring_confirmed": True,
                    "provider_identity_not_investigated": True,
                },
                "evaluator_metadata": {
                    "audio_playback_device_category": "headphones",
                    "colombian_spanish_familiarity": (
                        "strong" if evaluator_index < strong_count else "general"
                    ),
                    "evaluator_id": evaluator_id,
                    "optional_safe_notes": None,
                    "spanish_fluency_category": "fluent_latin_american",
                },
                "evaluator_package_id": f"package-{evaluator_index + 1:08d}",
                "forced_choices": forced_choices,
                "normalization_specification_hash": NORMALIZATION_HASH,
                "schema_version": "1.0.0",
                "scorecard_id": f"scorecard-{evaluator_index + 1:08d}",
                "status": "completed",
                "test_plan_hash": "fixture-plan-hash",
            }
        )
    return scorecards, mapping, sample_ids


def test_complete_listening_package_is_linked_blocked_and_canonical() -> None:
    result = validate_package(ROOT, as_of=date(2026, 7, 28))

    assert result == {
        "candidate_count": 3,
        "candidate_snapshot_hash": (
            "ccd1a1c7691770a1d2e064f6d39c957970730cf7f66ad5b4142807a0a00c6743"
        ),
        "generation_plan_hash": (
            "142eeb2fc5a61d065b523b9523ae0261bd74bef93be7b149e2f31ad1cfb61aa7"
        ),
        "generation_unit_count": 24,
        "research_snapshot_hash": (
            "7fcfb48e479a50e01b78b90175bddadb714a9b6ee161f28211b97898e61de005"
        ),
        "sample_count": 8,
        "script_snapshot_hash": (
            "6d689220c423425ede6f9309a973d422225ba5f3cdbc5264032fc5297644f515"
        ),
        "test_plan_hash": ("ba6f539046dfddb07475f9fe65b5bf74c53c22ed2e89366ead1d1523c8dbde5c"),
    }


def test_changed_research_artifact_breaks_snapshot_linkage(tmp_path: Path) -> None:
    package = _copy_package(tmp_path)
    path = package / "docs/research/tts-provider-evaluation.json"
    document = load_canonical_json(path)
    document["confidence"]["reason"] += " Changed fixture."
    _write_canonical(path, document)

    with pytest.raises(ListeningTestPlanValidationError, match="research snapshot hash"):
        validate_package(package, as_of=date(2026, 7, 28))


def test_candidate_rejects_unknown_provider_and_unknown_source() -> None:
    candidate, research, research_hash = _candidate_validation_inputs()
    unknown_provider = copy.deepcopy(candidate)
    unknown_provider["candidates"][0]["provider_id"] = "unknown"
    with pytest.raises(ListeningTestPlanValidationError, match="unknown provider"):
        validate_candidate_document(
            unknown_provider,
            as_of=date(2026, 7, 28),
            research_document=research,
            research_snapshot_hash=research_hash,
        )

    unknown_source = copy.deepcopy(candidate)
    unknown_source["candidates"][0]["source_ids"].append("missing-source")
    with pytest.raises(ListeningTestPlanValidationError, match="unknown source"):
        validate_candidate_document(
            unknown_source,
            as_of=date(2026, 7, 28),
            research_document=research,
            research_snapshot_hash=research_hash,
        )


def test_stale_research_blocks_candidate_eligibility() -> None:
    candidate, research, research_hash = _candidate_validation_inputs()

    with pytest.raises(ListeningTestPlanValidationError, match="stale"):
        validate_candidate_document(
            candidate,
            as_of=date(2026, 10, 29),
            research_document=research,
            research_snapshot_hash=research_hash,
        )


def test_candidates_are_exact_unique_and_unknowns_remain_null() -> None:
    candidate, research, research_hash = _candidate_validation_inputs()
    validate_candidate_document(
        candidate,
        as_of=date(2026, 7, 28),
        research_document=research,
        research_snapshot_hash=research_hash,
    )

    assert {item["provider_id"] for item in candidate["candidates"]} == {
        "aws_polly",
        "azure",
        "google",
    }
    assert {item["provider_id"] for item in candidate["excluded_providers"]} == {
        "elevenlabs",
        "openai",
    }
    assert all(item["provider_model"] is None for item in candidate["candidates"])
    assert all(item["provider_voice"] is None for item in candidate["candidates"])
    assert all(item["region"] is None for item in candidate["candidates"])
    assert all(item["maximum_authorized_cost"] is None for item in candidate["candidates"])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("eligibility_status", "eligible", "blocked"),
        ("commercial_review_status", "approved", "commercial"),
        ("retention_review_status", "approved", "retention"),
        ("availability_status", "available", "availability"),
        ("target_sample_rate_hz", 44100, "audio format"),
    ],
)
def test_candidate_cannot_look_eligible(
    field: str,
    value: Any,
    message: str,
) -> None:
    candidate, research, research_hash = _candidate_validation_inputs()
    mutated = copy.deepcopy(candidate)
    mutated["candidates"][0][field] = value

    with pytest.raises(ListeningTestPlanValidationError, match=message):
        validate_candidate_document(
            mutated,
            as_of=date(2026, 7, 28),
            research_document=research,
            research_snapshot_hash=research_hash,
        )


def test_script_hashes_counts_accents_and_punctuation_are_deterministic() -> None:
    script = _artifact("tts-listening-test-script.es.json")
    validate_script_document(script)

    colombia = next(
        sample for sample in script["samples"] if sample["sample_id"] == "es-colombia-004"
    )
    punctuation = next(
        sample for sample in script["samples"] if sample["sample_id"] == "es-punctuation-005"
    )
    assert "Bogotá" in colombia["text"]
    assert "Chocó" in colombia["text"]
    assert "¿" in punctuation["text"]
    assert "…" in punctuation["text"]
    assert (
        normalized_text_facts(colombia["text"])["normalized_text_hash"]
        == colombia["normalized_text_hash"]
    )


def test_text_normalizer_preserves_nfc_accents_and_meaningful_punctuation() -> None:
    assert normalize_test_text("  Bogota\u0301… ¿sí?  ") == "Bogotá… ¿sí?"
    with pytest.raises(ListeningTestSupportError, match="single-line"):
        normalize_test_text("primera línea\nsegunda línea")


def test_script_rejects_duplicate_sample_and_external_submission_change() -> None:
    script = _artifact("tts-listening-test-script.es.json")
    duplicate = copy.deepcopy(script)
    duplicate["samples"][1]["sample_id"] = duplicate["samples"][0]["sample_id"]
    with pytest.raises(ListeningTestPlanValidationError, match="duplicate"):
        validate_script_document(duplicate)

    blocked = copy.deepcopy(script)
    blocked["samples"][0]["allowed_for_external_submission"] = False
    with pytest.raises(ListeningTestPlanValidationError, match="public-safe"):
        validate_script_document(blocked)


def test_generation_plan_is_complete_text_free_and_fail_closed() -> None:
    plan = _artifact("tts-listening-test-generation-plan.json")
    candidates = _artifact("tts-listening-test-candidates.json")
    script = _artifact("tts-listening-test-script.es.json")

    validate_generation_plan_document(
        plan,
        candidate_document=candidates,
        candidate_snapshot_hash=plan["candidate_snapshot_hash"],
        research_snapshot_hash=plan["research_snapshot_hash"],
        script_document=script,
        script_snapshot_hash=plan["script_snapshot_hash"],
    )
    assert len(plan["generation_units"]) == 24
    assert all(
        unit["authorization_status"] == "not_authorized" for unit in plan["generation_units"]
    )
    assert all(unit["execution_status"] == "blocked" for unit in plan["generation_units"])
    assert all(unit["output_status"] == "absent" for unit in plan["generation_units"])
    assert all("text" not in unit for unit in plan["generation_units"])
    azure_units = [
        unit for unit in plan["generation_units"] if unit["candidate_id"] == "candidate-azure-01"
    ]
    assert all(unit["pricing_status"] == "unknown" for unit in azure_units)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authorization_status", "authorized"),
        ("execution_status", "ready"),
        ("output_status", "present"),
        ("maximum_authorized_cost", "0.01"),
    ],
)
def test_generation_unit_cannot_become_executable(field: str, value: Any) -> None:
    plan = _artifact("tts-listening-test-generation-plan.json")
    candidates = _artifact("tts-listening-test-candidates.json")
    script = _artifact("tts-listening-test-script.es.json")
    mutated = copy.deepcopy(plan)
    mutated["generation_units"][0][field] = value

    with pytest.raises(ListeningTestPlanValidationError, match="deterministic"):
        validate_generation_plan_document(
            mutated,
            candidate_document=candidates,
            candidate_snapshot_hash=plan["candidate_snapshot_hash"],
            research_snapshot_hash=plan["research_snapshot_hash"],
            script_document=script,
            script_snapshot_hash=plan["script_snapshot_hash"],
        )


def test_committed_authorization_template_cannot_look_live() -> None:
    authorization = _artifact("tts-listening-test-authorization.template.json")
    mutated = copy.deepcopy(authorization)
    mutated["status"] = "authorized"

    with pytest.raises(ListeningTestPlanValidationError, match="appears live"):
        validate_authorization_template(
            mutated,
            candidate_snapshot_hash=authorization["candidate_snapshot_hash"],
            generation_plan_hash=authorization["generation_plan_hash"],
            research_snapshot_hash=authorization["research_snapshot_hash"],
            script_snapshot_hash=authorization["script_snapshot_hash"],
            expected_test_plan_hash=authorization["test_plan_hash"],
        )


def test_committed_results_template_cannot_contain_live_aggregates(tmp_path: Path) -> None:
    package = _copy_package(tmp_path)
    path = package / "docs/research/tts-listening-results.template.json"
    results = load_canonical_json(path)
    results["median_scores"] = [{"candidate": "blind-fixture", "score": "4.0"}]
    _write_canonical(path, results)

    with pytest.raises(ListeningTestPlanValidationError, match="live evaluation state"):
        validate_package(package, as_of=date(2026, 7, 28))


def test_future_budget_uses_decimal_and_respects_every_ceiling() -> None:
    validate_future_budget(_future_authorization(), _unit_costs())

    unknown = _unit_costs()
    unknown[0]["pricing_status"] = "unknown"
    with pytest.raises(ListeningTestPlanValidationError, match="unverified price"):
        validate_future_budget(_future_authorization(), unknown)

    over_unit = _unit_costs()
    over_unit[0]["estimated_maximum_cost"] = "0.21"
    with pytest.raises(ListeningTestPlanValidationError, match="unit cost"):
        validate_future_budget(_future_authorization(), over_unit)

    over_policy = _future_authorization()
    over_policy["total_budget_ceiling"] = "10.01"
    with pytest.raises(ListeningTestPlanValidationError, match="USD 10"):
        validate_future_budget(over_policy, _unit_costs())


def test_policy_credit_balance_and_subscription_never_authorize() -> None:
    budget = _artifact("tts-listening-test-generation-plan.json")["policy_budget"]

    assert budget["policy_maximum"] == "10.00"
    assert budget["current_authorized_amount"] is None
    assert budget["free_credit_authorizes_execution"] is False
    assert budget["subscription_allowance_authorizes_execution"] is False
    assert budget["account_balance_authorizes_execution"] is False


def test_blind_ids_and_orders_are_deterministic_opaque_and_evaluator_specific() -> None:
    first = blind_sample_id(
        candidate_id="candidate-azure-01",
        evaluator_id="eval-00000001",
        run_id="run-fixture",
        sample_id="es-neutral-001",
        secret=FAKE_BLIND_KEY,
    )
    repeat = blind_sample_id(
        candidate_id="candidate-azure-01",
        evaluator_id="eval-00000001",
        run_id="run-fixture",
        sample_id="es-neutral-001",
        secret=FAKE_BLIND_KEY,
    )
    assert first == repeat
    assert "azure" not in first
    assert "neutral" not in first

    first_order = evaluator_blind_order(
        candidate_ids=["candidate-azure-01", "candidate-google-01", "candidate-polly-01"],
        evaluator_id="eval-00000001",
        run_id="run-fixture",
        sample_ids=["es-neutral-001", "es-numbers-002"],
        secret=FAKE_BLIND_KEY,
    )
    second_order = evaluator_blind_order(
        candidate_ids=["candidate-azure-01", "candidate-google-01", "candidate-polly-01"],
        evaluator_id="eval-00000002",
        run_id="run-fixture",
        sample_ids=["es-neutral-001", "es-numbers-002"],
        secret=FAKE_BLIND_KEY,
    )
    assert first_order != second_order
    assert len(first_order) == len(set(first_order)) == 6
    assert len(second_order) == len(set(second_order)) == 6


@pytest.mark.parametrize("secret", [b"", b"default", b"0" * 32])
def test_blinding_rejects_empty_default_or_structurally_unsafe_keys(secret: bytes) -> None:
    with pytest.raises(ListeningTestSupportError, match="HMAC key"):
        blind_sample_id(
            candidate_id="candidate-azure-01",
            evaluator_id="eval-00000001",
            run_id="run-fixture",
            sample_id="es-neutral-001",
            secret=secret,
        )


def test_aggregation_enforces_minimums_and_returns_descriptive_medians() -> None:
    scorecards, mapping, sample_ids = _scorecard_fixture()

    result = aggregate_scorecards(
        blind_mapping=mapping,
        normalization_specification_hash=NORMALIZATION_HASH,
        required_sample_ids=sample_ids,
        scorecards=scorecards,
    )

    assert result["completed_evaluator_count"] == 5
    assert result["strong_colombian_familiarity_count"] == 3
    assert result["recommendation_status"] == "descriptive_results_only"
    for candidate in result["candidate_descriptive_results"].values():
        assert candidate["median_weighted_score"] == "4.0"
        assert candidate["mean_weighted_score"] == "4.0"
        assert candidate["weighted_score_iqr"] == "0.0"
        assert candidate["disqualified"] is False


def test_aggregation_rejects_too_few_listeners_or_colombian_familiarity() -> None:
    scorecards, mapping, sample_ids = _scorecard_fixture(evaluator_count=4, strong_count=3)
    with pytest.raises(ListeningTestSupportError, match="five"):
        aggregate_scorecards(
            blind_mapping=mapping,
            normalization_specification_hash=NORMALIZATION_HASH,
            required_sample_ids=sample_ids,
            scorecards=scorecards,
        )

    scorecards, mapping, sample_ids = _scorecard_fixture(strong_count=2)
    with pytest.raises(ListeningTestSupportError, match="three"):
        aggregate_scorecards(
            blind_mapping=mapping,
            normalization_specification_hash=NORMALIZATION_HASH,
            required_sample_ids=sample_ids,
            scorecards=scorecards,
        )


def test_aggregation_rejects_duplicate_or_incomplete_scorecards() -> None:
    scorecards, mapping, sample_ids = _scorecard_fixture()
    duplicate = copy.deepcopy(scorecards)
    duplicate[1]["evaluator_metadata"]["evaluator_id"] = duplicate[0]["evaluator_metadata"][
        "evaluator_id"
    ]
    with pytest.raises(ListeningTestSupportError, match="duplicate evaluator"):
        aggregate_scorecards(
            blind_mapping=mapping,
            normalization_specification_hash=NORMALIZATION_HASH,
            required_sample_ids=sample_ids,
            scorecards=duplicate,
        )

    incomplete = copy.deepcopy(scorecards)
    incomplete[0]["blind_sample_scores"].pop()
    with pytest.raises(ListeningTestSupportError, match="missing required"):
        aggregate_scorecards(
            blind_mapping=mapping,
            normalization_specification_hash=NORMALIZATION_HASH,
            required_sample_ids=sample_ids,
            scorecards=incomplete,
        )


def test_aggregation_bounds_scores_sanitizes_comments_and_checks_normalization() -> None:
    scorecards, mapping, sample_ids = _scorecard_fixture()
    invalid_score = copy.deepcopy(scorecards)
    invalid_score[0]["blind_sample_scores"][0]["category_scores"]["naturalness"] = "6"
    with pytest.raises(ListeningTestSupportError, match="within 1-5"):
        aggregate_scorecards(
            blind_mapping=mapping,
            normalization_specification_hash=NORMALIZATION_HASH,
            required_sample_ids=sample_ids,
            scorecards=invalid_score,
        )

    unsafe_comment = copy.deepcopy(scorecards)
    unsafe_comment[0]["blind_sample_scores"][0]["optional_safe_comment"] = (
        "See https://example.invalid"
    )
    with pytest.raises(ListeningTestSupportError, match="comment is unsafe"):
        aggregate_scorecards(
            blind_mapping=mapping,
            normalization_specification_hash=NORMALIZATION_HASH,
            required_sample_ids=sample_ids,
            scorecards=unsafe_comment,
        )

    inconsistent = copy.deepcopy(scorecards)
    inconsistent[0]["normalization_specification_hash"] = "0" * 64
    with pytest.raises(ListeningTestSupportError, match="normalization differs"):
        aggregate_scorecards(
            blind_mapping=mapping,
            normalization_specification_hash=NORMALIZATION_HASH,
            required_sample_ids=sample_ids,
            scorecards=inconsistent,
        )


def test_critical_failures_disqualify_without_selecting_a_provider() -> None:
    scorecards, mapping, sample_ids = _scorecard_fixture()
    reports_added = 0
    for scorecard in scorecards:
        for score in scorecard["blind_sample_scores"]:
            identity = mapping[score["blind_sample_id"]]
            if (
                identity["candidate_id"] == "candidate-azure-01"
                and identity["sample_id"] == "es-numbers-002"
                and reports_added < 2
            ):
                score["critical_failures"].append("critical_number_or_currency_error")
                reports_added += 1
                break

    result = aggregate_scorecards(
        blind_mapping=mapping,
        normalization_specification_hash=NORMALIZATION_HASH,
        required_sample_ids=sample_ids,
        scorecards=scorecards,
    )

    azure = result["candidate_descriptive_results"]["candidate-azure-01"]
    assert azure["disqualified"] is True
    assert "critical_number_or_currency_error" in azure["disqualification_reasons"]
    assert result["recommendation_status"] == ("descriptive_results_only_with_disqualifications")


@pytest.mark.parametrize(
    "unsafe",
    [
        {"api_key": "not-allowed"},
        {"authorization_header": "Bearer fixture"},
        {"cookie": "session=fixture"},
        {"signed_url": "fixture"},
        {"account_id": "fixture"},
        {"endpoint": "fixture"},
        {"hmac_seed": "fixture"},
        {"blind_decoding_map": {}},
    ],
)
def test_security_scanner_rejects_sensitive_plan_fields(unsafe: dict[str, Any]) -> None:
    with pytest.raises(ListeningTestPlanValidationError, match="forbidden sensitive key"):
        scan_forbidden_content(unsafe)


@pytest.mark.parametrize("raw", ['{"value":NaN}\n', '{"value":1.25}\n'])
def test_strict_loader_rejects_non_finite_or_float_json(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "unsafe.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ListeningTestPlanValidationError):
        load_canonical_json(path)


def test_strict_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"1.0.0","schema_version":"1.0.0"}\n', encoding="utf-8")

    with pytest.raises(ListeningTestPlanValidationError, match="duplicate JSON key"):
        load_canonical_json(path)


def test_listening_tooling_imports_no_network_cloud_audio_or_process_modules() -> None:
    forbidden = {
        "aiohttp",
        "audioop",
        "azure",
        "boto3",
        "botocore",
        "elevenlabs",
        "google.cloud",
        "http.client",
        "httpx",
        "openai",
        "requests",
        "socket",
        "subprocess",
        "urllib.request",
        "wave",
    }
    paths = [
        ROOT / "scripts/tts_listening_test_support.py",
        ROOT / "scripts/validate_tts_listening_test_plan.py",
    ]
    imported: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert not {
        module
        for module in imported
        if any(module == item or module.startswith(f"{item}.") for item in forbidden)
    }
