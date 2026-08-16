"""Fingerprint, duration, durable store, and reconciliation safety tests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.scripting.duration_policy import assess_narration_duration
from backend.src.production.scripting.models import ProductionScript, ProductionScriptScene
from backend.src.production.scripting.openrouter_reconciliation import (
    OpenRouterScriptingRequestReconciler,
)
from backend.src.production.scripting.openrouter_request import (
    OpenRouterScriptingFingerprintInput,
    OpenRouterScriptingRequestRecord,
    OpenRouterScriptingRequestStatus,
    OpenRouterScriptingValidationErrorCode,
    openrouter_scripting_request_fingerprint,
    openrouter_scripting_request_relative_path,
)
from backend.src.production.scripting.openrouter_request_store import (
    InMemoryOpenRouterScriptingRequestStore,
    LocalOpenRouterScriptingRequestStore,
    OpenRouterScriptingRequestConflictError,
    OpenRouterScriptingRequestCorruptError,
)
from backend.src.production.scripting.openrouter_serialization import (
    serialize_openrouter_scripting_request,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-0000000006c0")
PLAN_ID = UUID("30000000-0000-4000-8000-0000000006c0")


def fingerprint_input(**updates: object) -> OpenRouterScriptingFingerprintInput:
    values: dict[str, object] = {
        "model": "vendor/explicit-model",
        "source_prompt_sha256": "1" * 64,
        "source_plan_artifact_id": PLAN_ID,
        "source_plan_sha256": "2" * 64,
        "language": "es",
        "target_duration_seconds": 30,
        "aspect_ratio": "9:16",
        "scene_count": 2,
        "scripting_configuration_sha256": "3" * 64,
        "prompt_template_version": "2.0.0",
        "prompt_template_sha256": "4" * 64,
        "temperature": Decimal("0.2"),
        "max_output_tokens": 8192,
    }
    values.update(updates)
    return OpenRouterScriptingFingerprintInput.model_validate(values)


def prepared_record(*, attempt: int = 1, **updates: object) -> OpenRouterScriptingRequestRecord:
    identity = fingerprint_input(**updates)
    return OpenRouterScriptingRequestRecord(
        job_id=JOB_ID,
        attempt_number=attempt,
        fingerprint_input=identity,
        request_fingerprint=openrouter_scripting_request_fingerprint(identity),
        status=OpenRouterScriptingRequestStatus.PREPARED,
        estimated_cost_usd=Decimal("0.01"),
        maximum_authorized_cost_usd=Decimal("0.10"),
        prepared_at=NOW,
        fresh_submission_permitted=True,
        metadata={"raw_response_persisted": False},
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_prompt_sha256", "5" * 64),
        ("source_plan_sha256", "6" * 64),
        ("model", "vendor/changed"),
        ("target_duration_seconds", 60),
        ("temperature", Decimal("0.3")),
        ("max_output_tokens", 4096),
    ],
)
def test_request_fingerprint_changes_only_for_output_inputs(field: str, value: object) -> None:
    original = fingerprint_input()
    changed = fingerprint_input(**{field: value})
    assert openrouter_scripting_request_fingerprint(original) != (
        openrouter_scripting_request_fingerprint(changed)
    )


def test_request_fingerprint_has_no_attempt_time_key_or_machine_path() -> None:
    identity = fingerprint_input()
    serialized = identity.model_dump_json()
    assert openrouter_scripting_request_fingerprint(identity) == (
        openrouter_scripting_request_fingerprint(identity)
    )
    for forbidden in ("attempt", "timestamp", "api_key", "Authorization", "C:\\Users"):
        assert forbidden not in serialized


def test_historical_fingerprint_omits_new_compression_identity_fields() -> None:
    identity = fingerprint_input()
    historical_payload = identity.model_dump(
        mode="json",
        exclude={"request_purpose", "source_script_sha256"},
    )
    expected = hashlib.sha256(
        json.dumps(
            historical_payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    compression = fingerprint_input(
        request_purpose="narration_compression",
        source_script_sha256="5" * 64,
    )

    assert openrouter_scripting_request_fingerprint(identity) == expected
    assert openrouter_scripting_request_fingerprint(compression) != expected


@pytest.mark.parametrize("duration", [15, 30, 60])
def test_duration_guidance_assesses_15_30_60_seconds(duration: int) -> None:
    words = " ".join("palabra" for _ in range(int(duration * 2.5)))
    script = ProductionScript(
        source_plan_schema_version="1.0.0",
        title="Marte",
        language="es",
        target_duration_seconds=duration,
        tone="claro",
        opening_hook="Una mirada breve a Marte.",
        scenes=(
            ProductionScriptScene(
                scene_number=1,
                source_scene_number=1,
                heading="Marte",
                narration=words,
                estimated_duration_seconds=duration,
                delivery_style="natural",
                visual_intent="Planeta rojo en el espacio",
            ),
        ),
    )
    assessment = assess_narration_duration(
        narrations=tuple(scene.narration for scene in script.scenes),
        target_duration_seconds=script.target_duration_seconds,
        reading_speed_words_per_minute=150,
    )
    assert assessment.target_duration_seconds == duration
    assert assessment.narration_word_count == int(duration * 2.5)
    assert assessment.within_target_duration
    assert assessment.estimated_duration_ms <= duration * 1_000


def test_reference_narration_is_measured_as_over_target_guidance() -> None:
    narrations = (
        "¡Hola! ¿Sabías que un video corto puede ser tu mejor aliado? Hoy te mostraremos "
        "cómo crear un short educativo impactante.",
        "Para una empresa de desarrollo de software, la claridad es clave. Enfócate en un "
        "mensaje principal y sé conciso.",
        "Utiliza un lenguaje claro, ejemplos prácticos y un estilo visual atractivo que "
        "refleje la innovación de tu empresa.",
        "Considera la duración ideal, unos 25 segundos, y asegúrate de que el audio sea "
        "nítido y la edición dinámica.",
        "Con estos pasos, tendrás un video corto que educa, informa y conecta con tu "
        "audiencia. ¡Manos a la obra!",
    )
    assessment = assess_narration_duration(
        narrations=narrations,
        target_duration_seconds=25,
        reading_speed_words_per_minute=150,
    )

    assert assessment.narration_word_count == 95
    assert assessment.punctuation_count == 15
    assert assessment.estimated_duration_ms == 39_800
    assert assessment.estimated_duration_ms > 28_000
    assert assessment.target_duration_ms == 25_000
    assert not assessment.within_target_duration


def test_global_duration_is_authoritative_not_equal_scene_allocation() -> None:
    assessment = assess_narration_duration(
        narrations=(
            "uno dos tres cuatro cinco seis siete ocho nueve diez once doce",
            "uno dos tres",
        ),
        target_duration_seconds=8,
        reading_speed_words_per_minute=150,
    )

    assert assessment.estimated_duration_ms == 6_000
    assert assessment.within_target_duration


def test_duration_assessment_reports_exact_target_and_one_more_pause() -> None:
    words = " ".join("palabra" for _ in range(61))
    exact = assess_narration_duration(
        narrations=(f"{words},;:.!",),
        target_duration_seconds=25,
        reading_speed_words_per_minute=150,
    )
    over = assess_narration_duration(
        narrations=(f"{words},;:.!?",),
        target_duration_seconds=25,
        reading_speed_words_per_minute=150,
    )

    assert exact.estimated_duration_ms == 25_000
    assert exact.within_target_duration
    assert over.estimated_duration_ms == 25_120
    assert not over.within_target_duration


@pytest.mark.parametrize(
    ("word_count", "punctuation_count", "expected_duration_ms"),
    ((64, 7, 26_440), (72, 10, 30_000)),
)
def test_valid_over_target_narration_is_measured_without_becoming_invalid(
    word_count: int,
    punctuation_count: int,
    expected_duration_ms: int,
) -> None:
    assessment = assess_narration_duration(
        narrations=(
            " ".join(f"palabra{index}" for index in range(word_count))
            + ("!" * punctuation_count),
        ),
        target_duration_seconds=25,
        reading_speed_words_per_minute=150,
    )

    assert assessment.estimated_duration_ms == expected_duration_ms
    assert assessment.within_target_duration is False


def test_valid_under_target_narration_is_measured_without_minimum_occupancy_gate() -> None:
    assessment = assess_narration_duration(
        narrations=("Una narración breve pero completa explica el tema con claridad.",),
        target_duration_seconds=25,
        reading_speed_words_per_minute=150,
    )

    assert assessment.estimated_duration_ms < assessment.target_duration_ms
    assert assessment.within_target_duration is True


@pytest.mark.asyncio
async def test_local_request_store_is_atomic_canonical_and_conflict_safe(tmp_path) -> None:
    store = LocalOpenRouterScriptingRequestStore(tmp_path, max_bytes=100_000)
    prepared = prepared_record()
    await store.create(prepared)
    assert await store.read(job_id=JOB_ID, attempt_number=1) == prepared
    target = tmp_path.joinpath(*openrouter_scripting_request_relative_path(prepared).split("/"))
    assert target.read_bytes() == serialize_openrouter_scripting_request(prepared)
    assert not list(target.parent.glob("*.tmp"))
    with pytest.raises(OpenRouterScriptingRequestConflictError):
        await store.create(prepared)
    submitting = prepared.model_copy(
        update={
            "status": OpenRouterScriptingRequestStatus.SUBMITTING,
            "submission_started_at": NOW,
            "fresh_submission_permitted": False,
        }
    )
    await store.checkpoint(previous=prepared, current=submitting)
    assert (await store.read(job_id=JOB_ID, attempt_number=1)).status is (
        OpenRouterScriptingRequestStatus.SUBMITTING
    )


@pytest.mark.asyncio
async def test_local_store_rejects_corruption_duplicate_keys_and_wrong_fingerprint(
    tmp_path,
) -> None:
    store = LocalOpenRouterScriptingRequestStore(tmp_path, max_bytes=100_000)
    prepared = prepared_record()
    await store.create(prepared)
    target = tmp_path.joinpath(*openrouter_scripting_request_relative_path(prepared).split("/"))
    target.write_bytes(b'{"schema_version":"1.0.0","schema_version":"1.0.0"}')
    with pytest.raises(OpenRouterScriptingRequestCorruptError):
        await store.read(job_id=JOB_ID, attempt_number=1)
    with pytest.raises(ValidationError, match="fingerprint"):
        OpenRouterScriptingRequestRecord.model_validate(
            {**prepared.model_dump(mode="python"), "request_fingerprint": "0" * 64}
        )


@pytest.mark.asyncio
async def test_reconciliation_is_read_only_and_classifies_uncertain_and_stale() -> None:
    store = InMemoryOpenRouterScriptingRequestStore()
    prepared = prepared_record()
    await store.create(prepared)
    submitting = prepared.model_copy(
        update={
            "status": OpenRouterScriptingRequestStatus.SUBMITTING,
            "submission_started_at": NOW,
            "fresh_submission_permitted": False,
        }
    )
    await store.checkpoint(previous=prepared, current=submitting)
    before = dict(store.records)
    report = await OpenRouterScriptingRequestReconciler(store).reconcile(
        job_id=JOB_ID,
        expected_source_plan_sha256="9" * 64,
    )
    assert report.submitting_interrupted
    assert report.source_plan_changed
    assert report.manual_intervention_required
    assert not report.automatic_submission_safe
    assert store.records == before


@pytest.mark.asyncio
async def test_reconciliation_reports_missing_request_model_and_orphan_state() -> None:
    empty = InMemoryOpenRouterScriptingRequestStore()
    missing = await OpenRouterScriptingRequestReconciler(empty).reconcile(job_id=JOB_ID)
    assert missing.issues == ("missing_request_record",)
    assert missing.automatic_submission_safe
    assert not missing.manual_intervention_required

    store = InMemoryOpenRouterScriptingRequestStore()
    await store.create(prepared_record())
    report = await OpenRouterScriptingRequestReconciler(store).reconcile(
        job_id=JOB_ID,
        expected_model="different/model",
        source_plan_present=False,
    )
    assert report.model_mismatch
    assert "orphan_request_record" in report.issues
    assert report.manual_intervention_required


def test_money_rejects_float_and_serializes_as_decimal_string() -> None:
    with pytest.raises(ValidationError, match="must not use float"):
        OpenRouterScriptingRequestRecord.model_validate(
            {**prepared_record().model_dump(mode="python"), "estimated_cost_usd": 0.01}
        )
    content = serialize_openrouter_scripting_request(prepared_record())
    assert b'"estimated_cost_usd":"0.01"' in content
    assert b"api_key" not in content
    assert hashlib.sha256(content).hexdigest()


def test_legacy_request_record_without_diagnostics_remains_readable() -> None:
    historical = prepared_record().model_copy(
        update={
            "status": OpenRouterScriptingRequestStatus.FAILED,
            "submission_started_at": NOW,
            "terminal_at": NOW,
            "fresh_submission_permitted": False,
            "safe_error_code": "invalid_structured_output",
        }
    ).model_dump(mode="python")
    for field in (
        "validation_error_code",
        "validation_error_path",
        "validation_error_message",
        "http_status",
        "requested_model",
    ):
        historical.pop(field)
    restored = OpenRouterScriptingRequestRecord.model_validate(historical)
    assert restored.schema_version == "1.0.0"
    assert restored.status is OpenRouterScriptingRequestStatus.FAILED
    assert restored.safe_error_code == "invalid_structured_output"
    assert restored.validation_error_code is None
    assert restored.validation_error_path is None
    assert restored.validation_error_message is None
    assert restored.http_status is None
    assert restored.requested_model is None


def test_historical_generic_expansion_contract_diagnostic_remains_readable() -> None:
    historical = prepared_record(
        request_purpose="narration_expansion",
        source_script_sha256="5" * 64,
    ).model_copy(
        update={
            "status": OpenRouterScriptingRequestStatus.FAILED,
            "submission_started_at": NOW,
            "terminal_at": NOW,
            "fresh_submission_permitted": False,
            "safe_error_code": "invalid_structured_output",
            "validation_error_code": (
                OpenRouterScriptingValidationErrorCode.NARRATION_EXPANSION_CONTRACT
            ),
            "validation_error_path": "scenes",
            "validation_error_message": (
                "narration expansion does not match the source script"
            ),
        }
    )

    restored = OpenRouterScriptingRequestRecord.model_validate_json(
        historical.model_dump_json()
    )

    assert restored.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.NARRATION_EXPANSION_CONTRACT
    )
    assert "expected_scene_numbers" not in restored.metadata


def test_historical_generic_compression_contract_diagnostic_remains_readable() -> None:
    historical = prepared_record(
        request_purpose="narration_compression",
        source_script_sha256="6" * 64,
    ).model_copy(
        update={
            "status": OpenRouterScriptingRequestStatus.FAILED,
            "submission_started_at": NOW,
            "terminal_at": NOW,
            "fresh_submission_permitted": False,
            "safe_error_code": "invalid_structured_output",
            "validation_error_code": (
                OpenRouterScriptingValidationErrorCode.NARRATION_COMPRESSION_CONTRACT
            ),
            "validation_error_path": "scenes",
            "validation_error_message": (
                "narration compression does not match the source script"
            ),
        }
    )

    restored = OpenRouterScriptingRequestRecord.model_validate_json(
        historical.model_dump_json()
    )

    assert restored.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.NARRATION_COMPRESSION_CONTRACT
    )
    assert "minimum_total_words" not in restored.metadata


def test_validation_diagnostics_are_closed_bounded_and_failed_only() -> None:
    failed = prepared_record().model_copy(
        update={
            "status": OpenRouterScriptingRequestStatus.FAILED,
            "submission_started_at": NOW,
            "terminal_at": NOW,
            "fresh_submission_permitted": False,
            "safe_error_code": "invalid_structured_output",
            "validation_error_code": (
                OpenRouterScriptingValidationErrorCode.PRODUCTION_SCRIPT_SCHEMA
            ),
            "validation_error_path": "scenes[1].narration",
            "validation_error_message": "field required",
        }
    )
    assert failed.validation_error_code is (
        OpenRouterScriptingValidationErrorCode.PRODUCTION_SCRIPT_SCHEMA
    )
    with pytest.raises(ValidationError):
        OpenRouterScriptingRequestRecord.model_validate(
            {
                **failed.model_dump(mode="python"),
                "validation_error_message": "x" * 241,
            }
        )
    with pytest.raises(ValidationError, match="requires failed status"):
        OpenRouterScriptingRequestRecord.model_validate(
            {
                **prepared_record().model_dump(mode="python"),
                "validation_error_code": "production_script_schema",
            }
        )
