"""Generate one provider-configured local end-to-end ORION video."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID

from pydantic import ValidationError

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.services.exceptions import ProductionApplicationError
from backend.src.production.composition import build_production_container
from backend.src.production.composition.schema import ensure_production_schema
from backend.src.production.domain.enums import ProductionJobStatus
from backend.src.production.infrastructure.persistence.session import sqlite_url_from_path
from backend.src.production.local_mvp import (
    LOCAL_MVP_MODE,
    LocalMvpApplication,
    LocalMvpProgress,
    LocalMvpRequest,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orion-generate-video",
        description="Run ORION's local pipeline through real FFmpeg validation.",
    )
    parser.add_argument("--prompt", help="Natural-language video idea")
    parser.add_argument("--title", help="Optional local production title")
    parser.add_argument(
        "--target-duration",
        type=int,
        default=8,
        help="Target duration in seconds (4-60; default: 8)",
    )
    parser.add_argument(
        "--aspect-ratio",
        choices=("9:16", "16:9", "1:1"),
        default="9:16",
    )
    parser.add_argument(
        "--scene-count",
        type=int,
        default=2,
        help="Planned scene count (1-50; default: 2)",
    )
    parser.add_argument("--project-id", help="Optional safe local project identifier")
    parser.add_argument("--resume-job-id", type=UUID, help="Resume an existing durable job")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Explicitly retry a failed durable job before resuming it",
    )
    parser.add_argument("--mode", choices=(LOCAL_MVP_MODE,), default=LOCAL_MVP_MODE)
    parser.add_argument(
        "--output-summary",
        action="store_true",
        help="Print the final report as JSON",
    )
    return parser


def local_mvp_settings() -> Settings:
    discovered = Settings()
    database_url = sqlite_url_from_path(discovered.ORION_HOME / "orion.db")
    return Settings(
        ORION_DATABASE_URL=database_url,
        ORION_PRODUCTION_AUTO_MIGRATE=True,
        ORION_PRODUCTION_WORKER_ENABLED=False,
        ORION_PLANNING_PROVIDER="simulated",
        ORION_SCRIPTING_PROVIDER=discovered.ORION_SCRIPTING_PROVIDER,
        ORION_SCRIPTING_MODEL=discovered.ORION_SCRIPTING_MODEL,
        ORION_SCRIPTING_API_KEY=discovered.ORION_SCRIPTING_API_KEY,
        ORION_OPENROUTER_API_KEY=discovered.ORION_OPENROUTER_API_KEY,
        ORION_SCRIPTING_BASE_URL=discovered.ORION_SCRIPTING_BASE_URL,
        ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS=(
            discovered.ORION_SCRIPTING_ALLOW_BILLABLE_REQUESTS
        ),
        ORION_SCRIPTING_ESTIMATED_COST_USD=(discovered.ORION_SCRIPTING_ESTIMATED_COST_USD),
        ORION_SCRIPTING_MAX_ESTIMATED_COST_USD=(discovered.ORION_SCRIPTING_MAX_ESTIMATED_COST_USD),
        ORION_SCRIPTING_TIMEOUT_SECONDS=discovered.ORION_SCRIPTING_TIMEOUT_SECONDS,
        ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS=(discovered.ORION_SCRIPTING_MAX_TRANSPORT_ATTEMPTS),
        ORION_SCRIPTING_MAX_OUTPUT_TOKENS=discovered.ORION_SCRIPTING_MAX_OUTPUT_TOKENS,
        ORION_SCRIPTING_TEMPERATURE=discovered.ORION_SCRIPTING_TEMPERATURE,
        ORION_SCRIPTING_MAX_RESPONSE_BYTES=(discovered.ORION_SCRIPTING_MAX_RESPONSE_BYTES),
        ORION_SCRIPTING_MAX_REQUEST_RECORD_BYTES=(
            discovered.ORION_SCRIPTING_MAX_REQUEST_RECORD_BYTES
        ),
        ORION_SCENE_PLANNING_PROVIDER="simulated",
        ORION_VISUAL_ASSET_PLANNING_PROVIDER="simulated",
        ORION_IMAGE_ACQUISITION_PROVIDER=discovered.ORION_IMAGE_ACQUISITION_PROVIDER,
        ORION_IMAGE_ACQUISITION_MODEL=discovered.ORION_IMAGE_ACQUISITION_MODEL,
        ORION_IMAGE_ACQUISITION_API_KEY=discovered.ORION_IMAGE_ACQUISITION_API_KEY,
        ORION_IMAGE_ACQUISITION_BASE_URL=discovered.ORION_IMAGE_ACQUISITION_BASE_URL,
        ORION_IMAGE_ACQUISITION_ALLOW_BILLABLE_REQUESTS=(
            discovered.ORION_IMAGE_ACQUISITION_ALLOW_BILLABLE_REQUESTS
        ),
        ORION_IMAGE_ACQUISITION_ESTIMATED_COST_USD=(
            discovered.ORION_IMAGE_ACQUISITION_ESTIMATED_COST_USD
        ),
        ORION_IMAGE_ACQUISITION_MAX_ESTIMATED_COST_USD=(
            discovered.ORION_IMAGE_ACQUISITION_MAX_ESTIMATED_COST_USD
        ),
        ORION_IMAGE_ACQUISITION_MAX_REQUESTS_PER_JOB=(
            discovered.ORION_IMAGE_ACQUISITION_MAX_REQUESTS_PER_JOB
        ),
        ORION_VIDEO_CLIP_GENERATION_PROVIDER=(discovered.ORION_VIDEO_CLIP_GENERATION_PROVIDER),
        ORION_VIDEO_CLIP_GENERATION_MODEL=discovered.ORION_VIDEO_CLIP_GENERATION_MODEL,
        ORION_VIDEO_CLIP_GENERATION_RESOLUTION=(discovered.ORION_VIDEO_CLIP_GENERATION_RESOLUTION),
        ORION_VIDEO_CLIP_GENERATION_GENERATE_AUDIO=(
            discovered.ORION_VIDEO_CLIP_GENERATION_GENERATE_AUDIO
        ),
        ORION_VIDEO_CLIP_GENERATION_FRAME_RATE=(discovered.ORION_VIDEO_CLIP_GENERATION_FRAME_RATE),
        ORION_VIDEO_CLIP_GENERATION_DURATION_SECONDS=(
            discovered.ORION_VIDEO_CLIP_GENERATION_DURATION_SECONDS
        ),
        ORION_VIDEO_CLIP_GENERATION_OPENROUTER_API_KEY=(
            discovered.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_API_KEY
        ),
        ORION_VIDEO_CLIP_GENERATION_OPENROUTER_BASE_URL=(
            discovered.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_BASE_URL
        ),
        ORION_VIDEO_CLIP_GENERATION_OPENROUTER_TIMEOUT_SECONDS=(
            discovered.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_TIMEOUT_SECONDS
        ),
        ORION_VIDEO_CLIP_GENERATION_OPENROUTER_POLL_INTERVAL_SECONDS=(
            discovered.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_POLL_INTERVAL_SECONDS
        ),
        ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_POLL_SECONDS=(
            discovered.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_POLL_SECONDS
        ),
        ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_POLL_ATTEMPTS=(
            discovered.ORION_VIDEO_CLIP_GENERATION_OPENROUTER_MAX_POLL_ATTEMPTS
        ),
        ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_COST_USD=(
            discovered.ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_COST_USD
        ),
        ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_JOB_COST_USD=(
            discovered.ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_JOB_COST_USD
        ),
        ORION_VIDEO_CLIP_GENERATION_MAX_REQUESTS_PER_JOB=(
            discovered.ORION_VIDEO_CLIP_GENERATION_MAX_REQUESTS_PER_JOB
        ),
        ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS=(
            discovered.ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS
        ),
        ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER=(
            discovered.ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER
        ),
        ORION_ASSET_PUBLISHING_PUBLISHER=discovered.ORION_ASSET_PUBLISHING_PUBLISHER,
        ORION_ASSET_PUBLISHING_PUBLIC_ROOT=(discovered.ORION_ASSET_PUBLISHING_PUBLIC_ROOT),
        ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL=(discovered.ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL),
        ORION_ASSET_PUBLISHING_LIFETIME_SECONDS=(
            discovered.ORION_ASSET_PUBLISHING_LIFETIME_SECONDS
        ),
        ORION_SPEECH_GENERATION_PROVIDER=discovered.ORION_SPEECH_GENERATION_PROVIDER,
        ORION_SPEECH_GENERATION_VOICE=discovered.ORION_SPEECH_GENERATION_VOICE,
        ORION_SPEECH_GENERATION_LANGUAGE=discovered.ORION_SPEECH_GENERATION_LANGUAGE,
        ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS=(
            discovered.ORION_SPEECH_GENERATION_ALLOW_BILLABLE_REQUESTS
        ),
        ORION_SPEECH_GENERATION_REMOTE_PROVIDER=(
            discovered.ORION_SPEECH_GENERATION_REMOTE_PROVIDER
        ),
        ORION_SPEECH_GENERATION_REMOTE_MODEL=(discovered.ORION_SPEECH_GENERATION_REMOTE_MODEL),
        ORION_SPEECH_GENERATION_REMOTE_VOICE=(discovered.ORION_SPEECH_GENERATION_REMOTE_VOICE),
        ORION_SPEECH_GENERATION_REMOTE_ESTIMATED_COST=(
            discovered.ORION_SPEECH_GENERATION_REMOTE_ESTIMATED_COST
        ),
        ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST=(
            discovered.ORION_SPEECH_GENERATION_REMOTE_MAX_ESTIMATED_COST
        ),
        ORION_SPEECH_GENERATION_MAX_REQUESTS_PER_JOB=(
            discovered.ORION_SPEECH_GENERATION_MAX_REQUESTS_PER_JOB
        ),
        ORION_NARRATION_FITTING_PROVIDER=discovered.ORION_NARRATION_FITTING_PROVIDER,
        ORION_NARRATION_FITTING_MODEL=discovered.ORION_NARRATION_FITTING_MODEL,
        ORION_NARRATION_FITTING_ALLOW_BILLABLE_REQUESTS=(
            discovered.ORION_NARRATION_FITTING_ALLOW_BILLABLE_REQUESTS
        ),
        ORION_NARRATION_FITTING_MAX_ATTEMPTS=(discovered.ORION_NARRATION_FITTING_MAX_ATTEMPTS),
        ORION_NARRATION_FITTING_ESTIMATED_COST_USD_PER_ATTEMPT=(
            discovered.ORION_NARRATION_FITTING_ESTIMATED_COST_USD_PER_ATTEMPT
        ),
        ORION_NARRATION_FITTING_MAX_ESTIMATED_COST_USD_PER_ATTEMPT=(
            discovered.ORION_NARRATION_FITTING_MAX_ESTIMATED_COST_USD_PER_ATTEMPT
        ),
        ORION_NARRATION_FITTING_MAX_ESTIMATED_JOB_COST_USD=(
            discovered.ORION_NARRATION_FITTING_MAX_ESTIMATED_JOB_COST_USD
        ),
        ORION_NARRATION_FITTING_OPENROUTER_BASE_URL=(
            discovered.ORION_NARRATION_FITTING_OPENROUTER_BASE_URL
        ),
        ORION_NARRATION_FITTING_TIMEOUT_SECONDS=(
            discovered.ORION_NARRATION_FITTING_TIMEOUT_SECONDS
        ),
        ORION_NARRATION_FITTING_MAX_TRANSPORT_ATTEMPTS=(
            discovered.ORION_NARRATION_FITTING_MAX_TRANSPORT_ATTEMPTS
        ),
        ORION_NARRATION_FITTING_MAX_OUTPUT_TOKENS=(
            discovered.ORION_NARRATION_FITTING_MAX_OUTPUT_TOKENS
        ),
        ORION_NARRATION_FITTING_TEMPERATURE=(discovered.ORION_NARRATION_FITTING_TEMPERATURE),
        ORION_NARRATION_FITTING_MAX_RESPONSE_BYTES=(
            discovered.ORION_NARRATION_FITTING_MAX_RESPONSE_BYTES
        ),
        ORION_MUSIC_GENERATION_PROVIDER="simulated",
        ORION_SOUND_EFFECT_GENERATION_PROVIDER="simulated",
        ORION_RENDERER="ffmpeg",
    )


def _print_progress(item: LocalMvpProgress) -> None:
    print(
        f"[{item.stage.value}] attempt={item.attempt_number} "
        f"outcome={item.outcome} progress={item.progress_percent:.0f}% "
        f"artifacts={item.artifacts_emitted}"
        + (f" error={item.error_code}" if item.error_code else ""),
        file=sys.stderr,
        flush=True,
    )


async def _run(args: argparse.Namespace) -> int:
    if args.retry_failed and args.resume_job_id is None:
        raise ValueError("--retry-failed requires --resume-job-id")
    settings = local_mvp_settings()
    container = build_production_container(settings)
    try:
        await ensure_production_schema(settings, container.engine)
        if args.retry_failed and args.resume_job_id is not None:
            current = await container.get_job.execute(args.resume_job_id)
            if current.job.status in {
                ProductionJobStatus.FAILED,
                ProductionJobStatus.NEEDS_USER_ACTION,
            }:
                await container.retry_job.execute(args.resume_job_id)
        application = LocalMvpApplication(
            create_job=container.create_job,
            get_job=container.get_job,
            list_artifacts=container.list_artifacts,
            list_events=container.list_events,
            worker=container.worker,
            workspace_root=settings.PROJECTS_DIR,
            configured_renderer=settings.ORION_RENDERER,
            max_validation_manifest_bytes=(
                settings.ORION_FINAL_RENDER_VALIDATION_MAX_MANIFEST_BYTES
            ),
        )
        report = await application.run(
            LocalMvpRequest(
                mode=args.mode,
                prompt=args.prompt,
                title=args.title,
                target_duration_seconds=args.target_duration,
                scene_count_hint=args.scene_count,
                aspect_ratio=args.aspect_ratio,
                project_id=args.project_id,
                resume_job_id=args.resume_job_id,
            ),
            progress_callback=_print_progress,
        )
    finally:
        await container.aclose()

    if args.output_summary:
        print(
            json.dumps(
                report.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    elif report.success and report.output is not None:
        output = report.output
        fps = output.frame_rate_numerator / output.frame_rate_denominator
        print(f"Job: {report.job_id}")
        print(f"Status: {report.status.value}")
        print(f"MP4: {output.local_absolute_path}")
        print(f"Size: {output.size_bytes} bytes")
        print(f"SHA-256: {output.sha256}")
        print(
            f"Media: {output.duration_ms / 1000:.3f}s, "
            f"{output.width}x{output.height}, {fps:.3f} fps, "
            f"{output.video_codec}/{output.audio_codec or 'no-audio'}"
        )
        print(f"Final validation: {output.validation_artifact_id}")
        print(f"Elapsed: {report.elapsed_seconds:.3f}s")
    else:
        assert report.failure is not None
        print(f"Job: {report.job_id}", file=sys.stderr)
        print(
            f"Stopped: status={report.status.value} stage={report.final_stage.value} "
            f"error={report.failure.error_code} retryable={report.failure.retryable}",
            file=sys.stderr,
        )
        print(report.failure.recommended_action, file=sys.stderr)
    return 0 if report.success else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print(
            "ORION local execution was interrupted; durable state was preserved.", file=sys.stderr
        )
        return 130
    except (ProductionApplicationError, ValidationError, OSError, RuntimeError, ValueError) as exc:
        message = " ".join(str(exc).split())[:500]
        print(f"ORION local MVP could not start: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["local_mvp_settings", "main"]
