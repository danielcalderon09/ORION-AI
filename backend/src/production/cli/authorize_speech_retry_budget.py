"""Create one offline bounded speech retry-budget authorization."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import ValidationError

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.infrastructure.persistence import (
    create_production_engine,
    create_production_session_factory,
)
from backend.src.production.runtime.runtime_state_reader import RuntimeStateReader
from backend.src.production.speech_generation.retry_budget import (
    FilesystemSpeechRetryBudgetAuthorizationStore,
    SpeechRetryBudgetAuthorizationError,
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
        prog="orion-authorize-speech-retry-budget",
        description=(
            "Persist one job-scoped speech recovery budget without calling providers."
        ),
    )
    parser.add_argument("--job-id", type=UUID, required=True)
    parser.add_argument("--source-attempt", type=int, required=True)
    parser.add_argument("--new-max-requests", type=int, required=True)
    parser.add_argument("--new-max-job-cost-usd", type=_money, required=True)
    parser.add_argument(
        "--maximum-additional-provider-requests",
        type=int,
        required=True,
    )
    parser.add_argument("--maximum-additional-cost-usd", type=_money, required=True)
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
        base_cost = settings.ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST
        estimated_cost = settings.ORION_SPEECH_GENERATION_REMOTE_ESTIMATED_COST
        if base_cost is None or estimated_cost is None:
            raise SpeechRetryBudgetAuthorizationError(
                "current Settings do not define a remote speech budget"
            )
        target_attempt = args.source_attempt + 1
        if state.next_attempt_number(args.job_id, job.current_stage) != target_attempt:
            raise SpeechRetryBudgetAuthorizationError(
                "source attempt is not the latest durable stage attempt"
            )
        authorization, idempotent = (
            await FilesystemSpeechRetryBudgetAuthorizationStore(
                settings.PROJECTS_DIR
            ).authorize(
                job_id=args.job_id,
                job_status=job.status,
                current_stage=job.current_stage,
                error_code=job.error_code,
                source_stage_attempt=args.source_attempt,
                target_stage_attempt=target_attempt,
                base_maximum_requests_per_job=(
                    settings.ORION_SPEECH_GENERATION_MAX_REQUESTS_PER_JOB
                ),
                base_maximum_tts_job_cost_usd=base_cost,
                new_maximum_requests_per_job=args.new_max_requests,
                new_maximum_tts_job_cost_usd=args.new_max_job_cost_usd,
                maximum_additional_provider_requests=(
                    args.maximum_additional_provider_requests
                ),
                maximum_additional_estimated_cost_usd=(
                    args.maximum_additional_cost_usd
                ),
                estimated_cost_per_request_usd=estimated_cost,
                operator_id=args.operator_id,
                clock=lambda: datetime.now(UTC),
            )
        )
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "authorization_fingerprint": authorization.fingerprint,
                "effective_maximum_requests": (
                    authorization.new_maximum_requests_per_job
                ),
                "effective_maximum_tts_job_cost_usd": str(
                    authorization.new_maximum_tts_job_cost_usd
                ),
                "idempotent": idempotent,
                "job_id": str(authorization.job_id),
                "provider_request_performed": False,
                "target_stage_attempt": authorization.target_stage_attempt,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(_run(_parser().parse_args(argv)))
    except KeyboardInterrupt:
        return 130
    except (
        OSError,
        RuntimeError,
        SpeechRetryBudgetAuthorizationError,
        ValidationError,
        ValueError,
    ) as exc:
        message = " ".join(str(exc).split())[:500]
        print(f"Speech retry budget was not authorized: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
