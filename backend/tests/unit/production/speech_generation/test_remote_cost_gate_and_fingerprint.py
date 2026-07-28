from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.src.production.speech_generation.billable_gate import (
    SpeechBillableRequestGate,
    SpeechBillableRequestPolicy,
)
from backend.src.production.speech_generation.cost import (
    SpeechCostAuthorization,
    SpeechCostAuthorizationStatus,
    SpeechCostEstimator,
    SpeechPricingSnapshot,
    SpeechUsageMeasurement,
)
from backend.src.production.speech_generation.exceptions import (
    SpeechBillableAuthorizationError,
    SpeechCostEstimationError,
)
from backend.src.production.speech_generation.fingerprinting import (
    SpeechRemoteRequestFingerprintInput,
    speech_remote_request_fingerprint,
)
from backend.src.production.speech_generation.remote_capabilities import (
    SpeechPricingCapability,
    SpeechPricingUnit,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobStatus,
)
from backend.src.production.speech_generation.voice_selection import (
    SpeechVoiceSelector,
)
from backend.tests.unit.production.speech_generation.conftest import NOW
from backend.tests.unit.production.speech_generation.remote_test_helpers import (
    capability_snapshot,
    prepared_remote_record,
    remote_record_for_status,
    selection_request,
)


def _estimate(
    unit: SpeechPricingUnit,
    *,
    pricing: SpeechPricingCapability | None = None,
):
    snapshot = capability_snapshot(
        pricing_unit=(unit if pricing is None else SpeechPricingUnit.PER_CHARACTER)
    )
    selected = SpeechVoiceSelector().select(
        snapshot=snapshot,
        request=selection_request(),
    )
    model_pricing = pricing or snapshot.capabilities.models[0].pricing
    return SpeechCostEstimator().estimate(
        selection=selected,
        pricing=SpeechPricingSnapshot(
            provider=selected.provider,
            model=selected.model,
            voice=selected.voice,
            pricing=model_pricing,
            snapshot_at=NOW,
            source="obviously-fake-static-price",
        ),
        usage=SpeechUsageMeasurement(
            normalized_characters=13,
            utf8_bytes=15,
            estimated_tokens=4,
            estimated_duration_ms=90_000,
        ),
    )


def test_cost_estimator_supports_character_byte_duration_and_fixed_usage() -> None:
    assert _estimate(SpeechPricingUnit.PER_CHARACTER).estimated_billable_quantity == Decimal(13)
    assert _estimate(SpeechPricingUnit.PER_BYTE).estimated_billable_quantity == Decimal(15)
    assert _estimate(SpeechPricingUnit.PER_SECOND).estimated_billable_quantity == Decimal(90)
    assert _estimate(SpeechPricingUnit.PER_MINUTE).estimated_billable_quantity == Decimal("1.5")
    fixed = _estimate(
        SpeechPricingUnit.FIXED_PLUS_USAGE,
        pricing=SpeechPricingCapability(
            currency="USD",
            pricing_unit=SpeechPricingUnit.FIXED_PLUS_USAGE,
            usage_unit=SpeechPricingUnit.PER_CHARACTER,
            minimum_unit_price=Decimal("0.001"),
            maximum_unit_price=Decimal("0.002"),
            fixed_base_cost=Decimal("0.10"),
        ),
    )
    assert fixed.estimated_minimum_cost == Decimal("0.113")
    assert fixed.estimated_maximum_cost == Decimal("0.126")


def test_cost_estimate_is_a_bounded_decimal_range_and_rejects_float() -> None:
    estimate = _estimate(SpeechPricingUnit.PER_CHARACTER)
    assert estimate.estimated_minimum_cost == Decimal("0.013")
    assert estimate.estimated_maximum_cost == Decimal("0.026")
    assert isinstance(estimate.estimated_maximum_cost, Decimal)
    with pytest.raises(ValidationError, match="float"):
        SpeechPricingCapability(
            currency="USD",
            pricing_unit=SpeechPricingUnit.PER_REQUEST,
            minimum_unit_price=0.1,
            maximum_unit_price=Decimal("0.2"),
        )
    with pytest.raises(ValidationError, match="currency"):
        SpeechPricingCapability(
            currency="12!",
            pricing_unit=SpeechPricingUnit.PER_REQUEST,
            minimum_unit_price=Decimal("0.1"),
            maximum_unit_price=Decimal("0.2"),
        )


def test_unknown_pricing_fails_closed() -> None:
    snapshot = capability_snapshot(pricing_unit=SpeechPricingUnit.UNKNOWN)
    selected = SpeechVoiceSelector().select(
        snapshot=snapshot,
        request=selection_request(),
    )
    with pytest.raises(SpeechCostEstimationError, match="unknown"):
        SpeechCostEstimator().estimate(
            selection=selected,
            pricing=SpeechPricingSnapshot(
                provider=selected.provider,
                model=selected.model,
                voice=selected.voice,
                pricing=snapshot.capabilities.models[0].pricing,
                snapshot_at=NOW,
                source="fake",
            ),
            usage=SpeechUsageMeasurement(
                normalized_characters=1,
                utf8_bytes=1,
                estimated_duration_ms=1,
            ),
        )


def test_billable_gate_authorizes_only_explicit_durable_safe_fake_request() -> None:
    record = prepared_remote_record()
    result = SpeechBillableRequestGate().authorize(
        policy=SpeechBillableRequestPolicy(
            allow_billable_requests=True,
            remote_provider="fake-tts",
            provider_configuration_valid=True,
            live_adapter_available=True,
        ),
        record=record,
        capability_snapshot=capability_snapshot(),
        unresolved_uncertain_submission=False,
        authorized_at=NOW,
    )
    assert result.durable_status is RemoteSpeechJobStatus.PREPARED
    assert result.maximum_authorized_cost == Decimal("1.00")


def test_billable_authorization_equal_to_estimate_passes_and_below_fails() -> None:
    record = prepared_remote_record()
    exact = SpeechCostAuthorization(
        currency=record.estimated_cost.currency,
        maximum_authorized_cost=record.estimated_cost.estimated_maximum_cost,
        status=SpeechCostAuthorizationStatus.AUTHORIZED,
        authorized_at=NOW,
        authorization_reference="fake-exact-limit",
    )
    record_at_limit = type(record).model_validate(
        {**record.model_dump(mode="python"), "authorization": exact}
    )
    result = SpeechBillableRequestGate().authorize(
        policy=SpeechBillableRequestPolicy(
            allow_billable_requests=True,
            remote_provider="fake-tts",
            provider_configuration_valid=True,
            live_adapter_available=True,
        ),
        record=record_at_limit,
        capability_snapshot=capability_snapshot(),
        unresolved_uncertain_submission=False,
        authorized_at=NOW,
    )
    assert result.maximum_authorized_cost == result.estimated_maximum_cost

    below = exact.model_copy(
        update={
            "maximum_authorized_cost": (
                record.estimated_cost.estimated_maximum_cost - Decimal("0.001")
            )
        }
    )
    with pytest.raises(ValidationError, match="does not cover"):
        type(record).model_validate({**record.model_dump(mode="python"), "authorization": below})


@pytest.mark.parametrize(
    ("policy", "record_updates", "capability", "uncertain", "message"),
    [
        (SpeechBillableRequestPolicy(), {}, capability_snapshot(), False, "disabled"),
        (
            SpeechBillableRequestPolicy(allow_billable_requests=True),
            {},
            capability_snapshot(),
            False,
            "provider is disabled",
        ),
        (
            SpeechBillableRequestPolicy(
                allow_billable_requests=True,
                remote_provider="fake-tts",
                provider_configuration_valid=True,
                live_adapter_available=True,
            ),
            {"authorization": None},
            capability_snapshot(),
            False,
            "authorization",
        ),
        (
            SpeechBillableRequestPolicy(
                allow_billable_requests=True,
                remote_provider="fake-tts",
                provider_configuration_valid=True,
                live_adapter_available=True,
            ),
            {},
            None,
            False,
            "snapshot",
        ),
        (
            SpeechBillableRequestPolicy(
                allow_billable_requests=True,
                remote_provider="fake-tts",
                provider_configuration_valid=True,
                live_adapter_available=True,
            ),
            {},
            capability_snapshot(),
            True,
            "uncertain",
        ),
    ],
)
def test_billable_gate_rejects_missing_safety_conditions(
    policy: SpeechBillableRequestPolicy,
    record_updates: dict[str, object],
    capability,
    uncertain: bool,
    message: str,
) -> None:
    record = prepared_remote_record(**record_updates)
    with pytest.raises(SpeechBillableAuthorizationError, match=message):
        SpeechBillableRequestGate().authorize(
            policy=policy,
            record=record,
            capability_snapshot=capability,
            unresolved_uncertain_submission=uncertain,
            authorized_at=NOW,
        )


def test_billable_policy_cannot_infer_authorization_from_an_api_key() -> None:
    with pytest.raises(ValidationError):
        SpeechBillableRequestPolicy.model_validate(
            {
                "allow_billable_requests": True,
                "remote_provider": "fake-tts",
                "provider_configuration_valid": True,
                "live_adapter_available": True,
                "api_key": "obviously-fake",
            }
        )


def test_billable_gate_rejects_unknown_pricing_rejected_cost_and_missing_checkpoint() -> None:
    policy = SpeechBillableRequestPolicy(
        allow_billable_requests=True,
        remote_provider="fake-tts",
        provider_configuration_valid=True,
        live_adapter_available=True,
    )
    record = prepared_remote_record()
    unknown_estimate = record.estimated_cost.model_copy(
        update={"pricing_unit": SpeechPricingUnit.UNKNOWN}
    )
    unknown_record = type(record).model_validate(
        {**record.model_dump(mode="python"), "estimated_cost": unknown_estimate}
    )
    with pytest.raises(SpeechBillableAuthorizationError, match="unknown"):
        SpeechBillableRequestGate().authorize(
            policy=policy,
            record=unknown_record,
            capability_snapshot=capability_snapshot(),
            unresolved_uncertain_submission=False,
            authorized_at=NOW,
        )

    rejected = record.authorization.model_copy(
        update={"status": SpeechCostAuthorizationStatus.REJECTED}
    )
    rejected_record = type(record).model_validate(
        {**record.model_dump(mode="python"), "authorization": rejected}
    )
    with pytest.raises(SpeechBillableAuthorizationError, match="rejected"):
        SpeechBillableRequestGate().authorize(
            policy=policy,
            record=rejected_record,
            capability_snapshot=capability_snapshot(),
            unresolved_uncertain_submission=False,
            authorized_at=NOW,
        )

    with pytest.raises(SpeechBillableAuthorizationError, match="PREPARED"):
        SpeechBillableRequestGate().authorize(
            policy=policy,
            record=remote_record_for_status(RemoteSpeechJobStatus.SUBMITTING),
            capability_snapshot=capability_snapshot(),
            unresolved_uncertain_submission=False,
            authorized_at=NOW,
        )


def test_request_fingerprint_is_canonical_and_changes_with_output_inputs() -> None:
    record = prepared_remote_record()
    base = SpeechRemoteRequestFingerprintInput(
        source_script_artifact_id=record.source_script_artifact_id,
        source_script_sha256=record.source_script_sha256,
        segment_id=record.segment_id,
        normalized_text_hash=record.normalized_text_hash,
        provider=record.provider,
        model=record.model,
        voice=record.voice,
        language=record.language,
        speaking_rate=record.speaking_rate,
        audio_format=record.output_expectation.audio_format,
        sample_rate_hz=record.output_expectation.sample_rate_hz,
        channel_count=record.output_expectation.channel_count,
        capability_snapshot_hash=record.capability_snapshot_hash,
        pricing_snapshot_hash=record.pricing_snapshot_hash,
        generation_mode=record.generation_mode,
        options={"b": 2, "a": 1},
    )
    reordered = base.model_copy(update={"options": {"a": 1, "b": 2}})
    assert speech_remote_request_fingerprint(base) == speech_remote_request_fingerprint(reordered)
    for field, value in (
        ("normalized_text_hash", "3" * 64),
        ("voice", "changed-voice"),
        ("model", "changed-model"),
        ("audio_format", "mp3"),
        ("pricing_snapshot_hash", "4" * 64),
        ("capability_snapshot_hash", "5" * 64),
    ):
        changed = SpeechRemoteRequestFingerprintInput.model_validate(
            {**base.model_dump(mode="python"), field: value}
        )
        assert speech_remote_request_fingerprint(changed) != (
            speech_remote_request_fingerprint(base)
        )
