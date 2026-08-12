"""Create an offline, bounded budget overlay for one video retry lineage."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.infrastructure.persistence import (
    create_production_engine,
    create_production_session_factory,
)
from backend.src.production.runtime.runtime_state_reader import RuntimeStateReader
from backend.src.production.video_clip_generation.retry_budget import (
    FilesystemVideoRetryBudgetAuthorizationStore,
)


def _decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("cost must be a decimal value") from exc
    if not parsed.is_finite():
        raise argparse.ArgumentTypeError("cost must be finite")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orion-authorize-video-retry-budget",
        description="Authorize one bounded video-retry budget overlay without retrying.",
    )
    parser.add_argument("--job-id", type=UUID, required=True)
    parser.add_argument("--source-attempt", type=int, required=True)
    parser.add_argument("--new-video-job-cost-usd", type=_decimal, required=True)
    parser.add_argument("--maximum-additional-provider-requests", type=int, required=True)
    parser.add_argument("--maximum-additional-cost-usd", type=_decimal, required=True)
    parser.add_argument("--operator-id", required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    engine = create_production_engine(
        settings.production_database_url,
        echo=settings.ORION_DATABASE_ECHO,
    )
    try:
        state = RuntimeStateReader(create_production_session_factory(engine))
        job = state.load_job(args.job_id)
        retry = state.latest_retry_event(args.job_id)
        if retry is None:
            raise ValueError("job has no durable retry event")
        target_attempt = args.source_attempt + 1
        if retry.stage != job.current_stage or retry.next_attempt_number != target_attempt:
            raise ValueError("retry event differs from the requested recovery lineage")
        authorization, idempotent = await FilesystemVideoRetryBudgetAuthorizationStore(
            settings.PROJECTS_DIR
        ).authorize(
            job_id=args.job_id,
            job_status=job.status,
            current_stage=job.current_stage,
            error_code=job.error_code,
            source_stage_attempt=args.source_attempt,
            target_stage_attempt=target_attempt,
            new_authorized_video_job_cost_usd=args.new_video_job_cost_usd,
            maximum_additional_provider_requests=(args.maximum_additional_provider_requests),
            maximum_additional_estimated_provider_cost_usd=(args.maximum_additional_cost_usd),
            current_settings_video_job_ceiling_usd=(
                settings.ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_JOB_COST_USD
            ),
            operator_id=args.operator_id,
            clock=lambda: datetime.now(UTC),
        )
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "authorization_fingerprint": authorization.fingerprint,
                "effective_video_job_cost_usd": str(
                    authorization.new_authorized_video_job_cost_usd
                ),
                "idempotent": idempotent,
                "provider_request_performed": False,
                "target_stage_attempt": authorization.target_stage_attempt,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    main()
