"""Offline derivation of total job cost from immutable durable sidecars."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID

from backend.src.production.cost_accounting.models import (
    JobCostCategory,
    JobCostSource,
    ProductionJobCostSummary,
    ProviderCostRecord,
    VisualCostAudit,
    build_job_cost_summary,
)

_MAX_JSON_BYTES = 20_000_000


def derive_durable_job_cost_summary(
    *, job_id: UUID, job_root: Path
) -> ProductionJobCostSummary:
    """Derive a reproducible summary; never writes or contacts a provider."""

    records: list[ProviderCostRecord] = []
    records.extend(_scripting_records(job_root))
    records.extend(_speech_records(job_root))
    records.extend(_fitting_records(job_root))
    records.extend(_image_records(job_root))
    records.extend(_video_records(job_root))
    preliminary = build_job_cost_summary(job_id=job_id, records=tuple(records))
    visual_audit = _visual_audit(job_root, preliminary)
    return build_job_cost_summary(
        job_id=job_id,
        records=tuple(records),
        visual_cost_audit=visual_audit,
    )


def _scripting_records(root: Path) -> list[ProviderCostRecord]:
    records: list[ProviderCostRecord] = []
    for path in root.glob("scripting/attempt-*/openrouter-scripting-request*.json"):
        raw = _object(path)
        if raw.get("submission_started_at") is None:
            continue
        records.append(
            _record(
                category=JobCostCategory.SCRIPTING,
                identity=(
                    f"{_required_text(raw, 'request_fingerprint')}:"
                    f"{_required_text(raw, 'submission_started_at')}"
                ),
                provider="openrouter",
                estimated=_money(raw.get("estimated_cost_usd")),
                reported=_optional_money(raw.get("reported_cost_usd")),
            )
        )
    return records


def _speech_records(root: Path) -> list[ProviderCostRecord]:
    records: list[ProviderCostRecord] = []
    for path in root.glob("generating_narration/attempt-*/remote-speech-jobs/*.json"):
        raw = _object(path)
        if raw.get("submission_started_at") is None:
            continue
        estimate = _required_object(raw, "estimated_cost")
        reported_raw = raw.get("reported_cost")
        reported = (
            _optional_money(reported_raw.get("amount"))
            if isinstance(reported_raw, dict)
            else None
        )
        records.append(
            _record(
                category=JobCostCategory.SPEECH,
                identity=(
                    f"{_required_text(raw, 'request_fingerprint')}:"
                    f"{_required_text(raw, 'submission_started_at')}"
                ),
                provider=_required_text(raw, "provider"),
                estimated=_money(estimate.get("estimated_maximum_cost")),
                reported=reported,
            )
        )
    return records


def _fitting_records(root: Path) -> list[ProviderCostRecord]:
    records: list[ProviderCostRecord] = []
    for path in root.glob("generating_narration/attempt-*/speech-generation-manifest.json"):
        raw = _object(path)
        values = raw.get("fitting_records", [])
        if not isinstance(values, list):
            raise ValueError("speech fitting records must be an array")
        for value in values:
            if not isinstance(value, dict) or value.get("submission_started_at") is None:
                continue
            if value.get("strategy") == "deterministic_local":
                continue
            fingerprint = _required_text(value, "request_fingerprint")
            estimate = _money(value.get("estimated_cost_usd"))
            retry_count = _nonnegative_int(value.get("provider_retry_count", 0))
            reported = _optional_money(value.get("reported_cost_usd"))
            provider = _required_text(value, "provider")
            for provider_attempt in range(retry_count + 1):
                is_terminal_attempt = provider_attempt == retry_count
                records.append(
                    _record(
                        category=JobCostCategory.NARRATION_FITTING,
                        identity=f"{fingerprint}:provider-attempt-{provider_attempt + 1}",
                        provider=provider,
                        estimated=estimate,
                        reported=reported if is_terminal_attempt else None,
                    )
                )
    return records


def _image_records(root: Path) -> list[ProviderCostRecord]:
    records: list[ProviderCostRecord] = []
    manifests = tuple(root.glob("acquiring_assets/attempt-*/hybrid-asset-acquisition-manifest.json"))
    budget = _latest_object(
        root.glob("visual_asset_planning/attempt-*/aggregate-visual-budget-plan.json")
    )
    estimate_by_shot = {
        _required_text(item, "shot_id"): _money(item.get("estimated_cost_usd"))
        for item in budget.get("image_requirements", [])
        if isinstance(item, dict)
    } if budget is not None else {}
    for path in manifests:
        raw = _object(path)
        entries = raw.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("hybrid image entries must be an array")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("hybrid image entry must be an object")
            attempts = entry.get("provider_attempts")
            if isinstance(attempts, list) and attempts:
                for attempt in attempts:
                    if not isinstance(attempt, dict):
                        raise ValueError("hybrid image attempt must be an object")
                    records.append(
                        _record(
                            category=JobCostCategory.IMAGES,
                            identity=(
                                f"{_required_text(entry, 'request_identity')}:"
                                f"stage-{_nonnegative_int(attempt.get('stage_attempt_number'))}:"
                                f"provider-{_nonnegative_int(attempt.get('attempt_number'))}"
                            ),
                            provider=_required_text(attempt, "provider"),
                            estimated=_money(attempt.get("estimated_cost_usd")),
                            reported=_optional_money(attempt.get("reported_cost_usd")),
                        )
                    )
                continue
            if entry.get("provider_image_generated") is True:
                shot_id = _required_text(entry, "shot_id")
                estimate = estimate_by_shot.get(shot_id)
                if estimate is None:
                    raise ValueError("historical image request lacks durable estimate")
                records.append(
                    _record(
                        category=JobCostCategory.IMAGES,
                        identity=_required_text(entry, "request_identity"),
                        provider="historical_image_provider",
                        estimated=estimate,
                        reported=None,
                    )
                )
    return records


def _video_records(root: Path) -> list[ProviderCostRecord]:
    records: list[ProviderCostRecord] = []
    for path in root.glob("generating_video_clips/attempt-*/remote-jobs/*.json"):
        raw = _object(path)
        if raw.get("submission_started_at") is None:
            continue
        fingerprint = _required_text(raw, "provider_request_fingerprint")
        attempt = _nonnegative_int(raw.get("attempt_number"))
        submitted = _required_text(raw, "submission_started_at")
        records.append(
            _record(
                category=JobCostCategory.VIDEO,
                identity=f"{fingerprint}:attempt-{attempt}:{submitted}",
                provider=_required_text(raw, "provider"),
                estimated=_money(raw.get("estimated_cost_usd")),
                reported=_optional_money(raw.get("reported_cost_usd")),
            )
        )
    return records


def _visual_audit(
    root: Path, summary: ProductionJobCostSummary
) -> VisualCostAudit | None:
    budget = _latest_object(
        root.glob("visual_asset_planning/attempt-*/aggregate-visual-budget-plan.json")
    )
    if budget is None:
        return None
    images = summary.category(JobCostCategory.IMAGES).accounted_cost_usd
    video = summary.category(JobCostCategory.VIDEO).accounted_cost_usd
    accounted = images + video
    estimated = _money(budget.get("estimated_total_visual_cost_usd"))
    image_respected = images <= _money(budget.get("maximum_authorized_image_cost_usd"))
    video_respected = video <= _money(budget.get("maximum_authorized_video_cost_usd"))
    total_respected = accounted <= _money(
        budget.get("maximum_authorized_total_visual_cost_usd")
    )
    return VisualCostAudit(
        budget_estimated_cost_usd=estimated,
        accounted_cost_usd=accounted,
        cost_delta_usd=accounted - estimated,
        budget_exceeded=not total_respected,
        image_budget_respected=image_respected,
        video_budget_respected=video_respected,
        total_visual_budget_respected=total_respected,
    )


def _record(
    *,
    category: JobCostCategory,
    identity: str,
    provider: str,
    estimated: Decimal,
    reported: Decimal | None,
) -> ProviderCostRecord:
    return ProviderCostRecord(
        category=category,
        request_identity=identity,
        provider=provider,
        estimated_cost_usd=estimated,
        reported_cost_usd=reported,
        accounted_cost_usd=reported if reported is not None else estimated,
        cost_source=(
            JobCostSource.REPORTED
            if reported is not None
            else JobCostSource.ESTIMATED_FALLBACK
        ),
    )


def _latest_object(paths: Any) -> dict[str, Any] | None:
    candidates = tuple(paths)
    if not candidates:
        return None
    return _object(max(candidates, key=_attempt_key))


def _attempt_key(path: Path) -> tuple[int, str]:
    attempt = next(
        (
            int(part.removeprefix("attempt-"))
            for part in path.parts
            if part.startswith("attempt-") and part.removeprefix("attempt-").isdigit()
        ),
        0,
    )
    return attempt, path.as_posix()


def _object(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > _MAX_JSON_BYTES:
        raise ValueError("cost source artifact is missing or oversized")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("cost source artifact must be an object")
    return raw


def _required_object(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"cost source {key} must be an object")
    return value


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"cost source {key} must be text")
    return value


def _nonnegative_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("cost source attempt count must be a nonnegative integer")
    return value


def _optional_money(value: object) -> Decimal | None:
    return None if value is None else _money(value)


def _money(value: object) -> Decimal:
    if isinstance(value, (bool, float)) or value is None:
        raise ValueError("durable money must use exact Decimal text")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("durable money is invalid") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("durable money must be finite and nonnegative")
    return result
