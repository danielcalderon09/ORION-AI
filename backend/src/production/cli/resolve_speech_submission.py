"""Persist an explicit, offline resolution for an uncertain TTS submission."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.speech_generation.uncertainty_resolution import (
    SpeechSubmissionResolution,
    SpeechSubmissionResolutionProvenance,
    SpeechSubmissionResolutionStatus,
    SpeechUncertaintyResolver,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orion-resolve-speech-submission",
        description="Persist operator evidence for one uncertain speech submission.",
    )
    parser.add_argument("--job-id", type=UUID, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--segment-id", required=True)
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--request-fingerprint", required=True)
    parser.add_argument(
        "--resolution",
        choices=tuple(item.value for item in SpeechSubmissionResolutionStatus),
        required=True,
    )
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--evidence-reference")
    parser.add_argument("--provider-request-id")
    parser.add_argument("--remote-generation-id")
    parser.add_argument("--recovered-audio-sha256")
    parser.add_argument(
        "--acknowledge-new-submission",
        action="store_true",
        help="Required for confirmed_not_submitted; this does not submit anything.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    resolution = SpeechSubmissionResolution.create(
        job_id=args.job_id,
        attempt_number=args.attempt,
        segment_id=args.segment_id,
        scene_id=args.scene_id,
        request_fingerprint=args.request_fingerprint,
        resolution=SpeechSubmissionResolutionStatus(args.resolution),
        provenance=SpeechSubmissionResolutionProvenance.OPERATOR_ASSERTED,
        resolved_at=datetime.now(UTC),
        operator_id=args.operator_id,
        evidence_reference=args.evidence_reference,
        provider_request_id=args.provider_request_id,
        remote_generation_id=args.remote_generation_id,
        recovered_audio_sha256=args.recovered_audio_sha256,
        acknowledge_new_submission=args.acknowledge_new_submission,
    )
    await SpeechUncertaintyResolver(settings.PROJECTS_DIR).resolve(resolution)
    print(
        json.dumps(
            {
                "resolution": resolution.resolution.value,
                "fingerprint": resolution.fingerprint,
                "fresh_submission_eligible": resolution.fresh_submission_eligible,
                "provider_request_performed": False,
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
