"""Fingerprint, duration, durable store, and reconciliation safety tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.scripting.duration_policy import (
    validate_openrouter_duration_policy,
)
from backend.src.production.scripting.models import ProductionScript, ProductionScriptScene
from backend.src.production.scripting.openrouter_reconciliation import (
    OpenRouterScriptingRequestReconciler,
)
from backend.src.production.scripting.openrouter_request import (
    OpenRouterScriptingFingerprintInput,
    OpenRouterScriptingRequestRecord,
    OpenRouterScriptingRequestStatus,
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


@pytest.mark.parametrize("duration", [15, 30, 60])
def test_duration_policy_accepts_15_30_60_seconds(duration: int) -> None:
    words = " ".join("palabra" for _ in range(round(duration * 2.5)))
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
    assessment = validate_openrouter_duration_policy(
        script,
        reading_speed_words_per_minute=150,
    )
    assert assessment.target_duration_seconds == duration
    assert assessment.narration_word_count == round(duration * 2.5)


def test_duration_policy_rejects_insufficient_and_excessive_narration() -> None:
    def script(words: int) -> ProductionScript:
        return ProductionScript(
            source_plan_schema_version="1.0.0",
            title="Marte",
            language="es",
            target_duration_seconds=30,
            tone="claro",
            opening_hook="Marte en treinta segundos.",
            scenes=(
                ProductionScriptScene(
                    scene_number=1,
                    source_scene_number=1,
                    heading="Marte",
                    narration=" ".join("palabra" for _ in range(words)),
                    estimated_duration_seconds=30,
                    delivery_style="natural",
                    visual_intent="Marte",
                ),
            ),
        )

    with pytest.raises(ValueError, match="insufficient"):
        validate_openrouter_duration_policy(script(2), reading_speed_words_per_minute=150)
    with pytest.raises(ValueError, match="exceeds"):
        validate_openrouter_duration_policy(script(200), reading_speed_words_per_minute=150)


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
