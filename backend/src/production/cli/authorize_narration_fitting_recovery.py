"""Explicitly authorize bounded narration-fitting recovery for one failed job."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import ValidationError

from backend.src.production.cli.generate_video import local_mvp_settings
from backend.src.production.composition import build_production_container
from backend.src.production.composition.schema import ensure_production_schema
from backend.src.production.speech_generation.fitting_recovery import (
    NarrationFittingRecoveryAuthorizationError,
)


def _money(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("cost must be a decimal USD value") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("cost must be a positive finite USD value")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orion-authorize-narration-fitting-recovery",
        description=(
            "Persist an explicit recovery budget authorization without calling providers."
        ),
    )
    parser.add_argument("--job-id", type=UUID, required=True)
    parser.add_argument(
        "--maximum-job-cost-usd",
        type=_money,
        required=True,
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = local_mvp_settings()
    if (
        settings.ORION_NARRATION_FITTING_PROVIDER != "openrouter"
        or not settings.ORION_NARRATION_FITTING_ALLOW_BILLABLE_REQUESTS
        or settings.ORION_NARRATION_FITTING_ESTIMATED_COST_USD_PER_ATTEMPT is None
        or settings.ORION_NARRATION_FITTING_MAX_ESTIMATED_JOB_COST_USD is None
    ):
        raise NarrationFittingRecoveryAuthorizationError(
            "current narration fitting Settings do not authorize recovery"
        )
    container = build_production_container(settings)
    try:
        await ensure_production_schema(settings, container.engine)
        view = await container.get_job.execute(args.job_id)
        authorization, idempotent = (
            await container.narration_fitting_recovery_store.authorize(
                job_id=args.job_id,
                job_status=view.job.status,
                current_stage=view.job.current_stage,
                new_authorized_job_cost_usd=args.maximum_job_cost_usd,
                current_settings_authorization_usd=(
                    settings.ORION_NARRATION_FITTING_MAX_ESTIMATED_JOB_COST_USD
                ),
                estimated_cost_per_provider_request_usd=(
                    settings.ORION_NARRATION_FITTING_ESTIMATED_COST_USD_PER_ATTEMPT
                ),
                maximum_fitting_attempts=(
                    settings.ORION_NARRATION_FITTING_MAX_ATTEMPTS
                ),
                maximum_provider_retries=(
                    settings.ORION_NARRATION_FITTING_MAX_PROVIDER_RETRIES
                ),
                clock=lambda: datetime.now(UTC),
            )
        )
    finally:
        await container.aclose()
    print(
        json.dumps(
            {
                "additional_authorized_cost_usd": str(
                    authorization.additional_authorized_cost_usd
                ),
                "fingerprint": authorization.fingerprint,
                "idempotent": idempotent,
                "job_id": str(authorization.job_id),
                "maximum_additional_provider_requests": (
                    authorization.maximum_additional_provider_requests
                ),
                "new_authorized_job_cost_usd": str(
                    authorization.new_authorized_job_cost_usd
                ),
                "source_attempt_number": authorization.source_attempt_number,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except (
        NarrationFittingRecoveryAuthorizationError,
        OSError,
        RuntimeError,
        ValidationError,
        ValueError,
    ) as exc:
        safe_message = " ".join(str(exc).split())[:500]
        print(f"Narration fitting recovery was not authorized: {safe_message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
