"""Offline total production job cost accounting tests."""

import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from backend.src.production.cost_accounting import (
    JobCostCategory,
    JobCostSource,
    ProviderCostRecord,
    VisualCostAudit,
    build_job_cost_summary,
    derive_durable_job_cost_summary,
)

JOB_ID = UUID("10000000-0000-4000-8000-000000001901")


def _record(
    category: JobCostCategory,
    identity: str,
    estimated: str,
    reported: str | None,
    provider: str = "openrouter",
) -> ProviderCostRecord:
    estimate = Decimal(estimated)
    report = Decimal(reported) if reported is not None else None
    return ProviderCostRecord(
        category=category,
        request_identity=identity,
        provider=provider,
        estimated_cost_usd=estimate,
        reported_cost_usd=report,
        accounted_cost_usd=report if report is not None else estimate,
        cost_source=(
            JobCostSource.REPORTED
            if report is not None
            else JobCostSource.ESTIMATED_FALLBACK
        ),
    )


def test_fully_reported_fixture_has_complete_coverage() -> None:
    records = tuple(
        _record(category, f"request-{index}", "0.01", "0.008")
        for index, category in enumerate(JobCostCategory, start=1)
    )
    summary = build_job_cost_summary(job_id=JOB_ID, records=records)

    assert summary.total_accounted_cost_usd == Decimal("0.040")
    assert summary.total_reported_cost_usd == Decimal("0.040")
    assert summary.total_estimated_fallback_cost_usd == 0
    assert summary.reported_cost_coverage_percent == Decimal("100.00")
    assert summary.fully_reported is True
    assert summary.fingerprint == summary.calculated_fingerprint()


def test_mixed_fixture_separates_reported_and_fallback_cost() -> None:
    records = (
        _record(JobCostCategory.SCRIPTING, "script-1", "0.001", "0.0007"),
        _record(JobCostCategory.SPEECH, "tts-1", "0.003", None),
        _record(JobCostCategory.NARRATION_FITTING, "fit-1", "0.001", "0.0001"),
        _record(JobCostCategory.IMAGES, "image-1", "0.04", "0.03"),
        _record(JobCostCategory.IMAGES, "image-2", "0.04", None),
        _record(JobCostCategory.VIDEO, "video-1", "0.18", "0.18"),
    )
    summary = build_job_cost_summary(job_id=JOB_ID, records=records)

    assert summary.total_reported_cost_usd == Decimal("0.2108")
    assert summary.total_estimated_fallback_cost_usd == Decimal("0.043")
    assert summary.total_accounted_cost_usd == Decimal("0.2538")
    assert summary.total_estimated_fallback_request_count == 2
    assert summary.reported_cost_coverage_percent == Decimal("66.67")
    assert summary.fully_reported is False


def test_failed_job_cost_does_not_require_completion_status() -> None:
    records = (
        _record(JobCostCategory.SCRIPTING, "script", "0.001", "0.0008"),
        _record(JobCostCategory.SPEECH, "tts", "0.002", None),
        _record(JobCostCategory.IMAGES, "image-1", "0.04", None),
        _record(JobCostCategory.IMAGES, "image-2", "0.04", "0.03"),
        _record(JobCostCategory.VIDEO, "failed-video-post", "0.18", None),
    )
    summary = build_job_cost_summary(job_id=JOB_ID, records=records)

    assert summary.total_request_count == 5
    assert summary.total_accounted_cost_usd == Decimal("0.2528")


def test_recovery_reuse_is_deduplicated_but_new_submission_is_counted() -> None:
    image_one = _record(JobCostCategory.IMAGES, "image-1", "0.04", "0.03")
    image_two = _record(JobCostCategory.IMAGES, "image-2", "0.04", None)
    image_retry = _record(JobCostCategory.IMAGES, "image-3-attempt-2", "0.04", "0.031")
    summary = build_job_cost_summary(
        job_id=JOB_ID,
        records=(image_one, image_two, image_one, image_two, image_retry),
    )

    assert summary.total_request_count == 3
    assert summary.total_accounted_cost_usd == Decimal("0.101")


def test_conflicting_duplicate_identity_fails_closed() -> None:
    with pytest.raises(ValueError, match="conflicting costs"):
        build_job_cost_summary(
            job_id=JOB_ID,
            records=(
                _record(JobCostCategory.IMAGES, "same", "0.04", None),
                _record(JobCostCategory.IMAGES, "same", "0.05", None),
            ),
        )


def test_visual_budget_audit_is_exposed_without_changing_enforcement() -> None:
    audit = VisualCostAudit(
        budget_estimated_cost_usd=Decimal("0.48"),
        accounted_cost_usd=Decimal("0.47"),
        cost_delta_usd=Decimal("-0.01"),
        budget_exceeded=False,
        image_budget_respected=True,
        video_budget_respected=True,
        total_visual_budget_respected=True,
    )
    summary = build_job_cost_summary(
        job_id=JOB_ID,
        records=(),
        visual_cost_audit=audit,
    )
    assert summary.visual_cost_audit == audit


def test_float_money_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not use float"):
        ProviderCostRecord(
            category=JobCostCategory.IMAGES,
            request_identity="image",
            provider="fixture",
            estimated_cost_usd=0.04,
            accounted_cost_usd="0.04",
            cost_source=JobCostSource.ESTIMATED_FALLBACK,
        )


def test_durable_reader_combines_all_stage_sources_offline(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "scripting/attempt-1/openrouter-scripting-request.json",
        {
            "submission_started_at": "2026-01-01T00:00:00Z",
            "request_fingerprint": "script-request",
            "estimated_cost_usd": "0.001",
            "reported_cost_usd": "0.0007",
        },
    )
    _write_json(
        tmp_path / "generating_narration/attempt-1/remote-speech-jobs/tts.json",
        {
            "submission_started_at": "2026-01-01T00:00:00Z",
            "request_fingerprint": "tts-request",
            "provider": "openrouter",
            "estimated_cost": {"estimated_maximum_cost": "0.001"},
            "reported_cost": None,
        },
    )
    _write_json(
        tmp_path / "generating_narration/attempt-1/speech-generation-manifest.json",
        {
            "fitting_records": [
                {
                    "submission_started_at": "2026-01-01T00:00:00Z",
                    "strategy": "remote_provider",
                    "request_fingerprint": "fitting-request",
                    "estimated_cost_usd": "0.001",
                    "reported_cost_usd": "0.0001",
                    "provider_retry_count": 0,
                    "provider": "openrouter",
                }
            ]
        },
    )
    _write_json(
        tmp_path / "visual_asset_planning/attempt-1/aggregate-visual-budget-plan.json",
        {
            "image_requirements": [
                {"shot_id": "scene-001-shot-001", "estimated_cost_usd": "0.04"}
            ],
            "estimated_total_visual_cost_usd": "0.22",
            "maximum_authorized_image_cost_usd": "0.12",
            "maximum_authorized_video_cost_usd": "0.40",
            "maximum_authorized_total_visual_cost_usd": "0.50",
        },
    )
    _write_json(
        tmp_path / "acquiring_assets/attempt-1/hybrid-asset-acquisition-manifest.json",
        {
            "entries": [
                {
                    "shot_id": "scene-001-shot-001",
                    "request_identity": "image-request",
                    "provider_image_generated": True,
                }
            ]
        },
    )
    _write_json(
        tmp_path / "generating_video_clips/attempt-1/remote-jobs/video.json",
        {
            "submission_started_at": "2026-01-01T00:00:00Z",
            "provider_request_fingerprint": "video-request",
            "attempt_number": 1,
            "provider": "openrouter",
            "estimated_cost_usd": "0.18",
            "reported_cost_usd": "0.18",
        },
    )

    summary = derive_durable_job_cost_summary(job_id=JOB_ID, job_root=tmp_path)

    assert summary.total_request_count == 5
    assert summary.total_accounted_cost_usd == Decimal("0.2218")
    assert summary.total_reported_cost_usd == Decimal("0.1808")
    assert summary.total_estimated_fallback_cost_usd == Decimal("0.041")
    assert summary.visual_cost_audit is not None
    assert summary.visual_cost_audit.accounted_cost_usd == Decimal("0.22")
    assert summary.visual_cost_audit.total_visual_budget_respected is True


def test_durable_reader_deduplicates_reuse_but_counts_real_tts_regeneration(
    tmp_path: Path,
) -> None:
    common = {
        "request_fingerprint": "tts-request",
        "provider": "openrouter",
        "estimated_cost": {"estimated_maximum_cost": "0.001"},
        "reported_cost": None,
    }
    original = {**common, "submission_started_at": "2026-01-01T00:00:00Z"}
    regenerated = {**common, "submission_started_at": "2026-01-02T00:00:00Z"}
    _write_json(
        tmp_path / "generating_narration/attempt-1/remote-speech-jobs/tts.json",
        original,
    )
    _write_json(
        tmp_path / "generating_narration/attempt-2/remote-speech-jobs/reused.json",
        original,
    )
    _write_json(
        tmp_path / "generating_narration/attempt-2/remote-speech-jobs/regenerated.json",
        regenerated,
    )

    summary = derive_durable_job_cost_summary(job_id=JOB_ID, job_root=tmp_path)

    assert summary.total_request_count == 2
    assert summary.total_accounted_cost_usd == Decimal("0.002")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
