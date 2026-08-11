"""Create one offline authorization for unresolved speech replacement."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.speech_generation.unresolved_replacement import (
    SpeechUnresolvedReplacementStore,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orion-authorize-unresolved-speech-replacement",
        description="Authorize one bounded replacement for an unresolved TTS request.",
    )
    parser.add_argument("--job-id", type=UUID, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--segment-id", required=True)
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--request-fingerprint", required=True)
    parser.add_argument("--resolution-fingerprint", required=True)
    parser.add_argument("--maximum-additional-provider-requests", type=int, required=True)
    parser.add_argument("--maximum-additional-cost-usd", type=Decimal, required=True)
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--acknowledge-duplicate-charge-risk", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    authorization = await SpeechUnresolvedReplacementStore(settings.PROJECTS_DIR).authorize(
        job_id=args.job_id,
        source_attempt_number=args.attempt,
        scene_id=args.scene_id,
        segment_id=args.segment_id,
        request_fingerprint=args.request_fingerprint,
        resolution_fingerprint=args.resolution_fingerprint,
        maximum_additional_provider_requests=args.maximum_additional_provider_requests,
        maximum_additional_estimated_cost_usd=args.maximum_additional_cost_usd,
        authorized_at=datetime.now(UTC),
        operator_id=args.operator_id,
        acknowledge_duplicate_charge_risk=args.acknowledge_duplicate_charge_risk,
    )
    print(
        json.dumps(
            {
                "authorization_fingerprint": authorization.fingerprint,
                "maximum_additional_provider_requests": (
                    authorization.maximum_additional_provider_requests
                ),
                "maximum_additional_estimated_cost_usd": str(
                    authorization.maximum_additional_estimated_cost_usd
                ),
                "provider_request_performed": False,
                "target_attempt_number": authorization.target_attempt_number,
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
