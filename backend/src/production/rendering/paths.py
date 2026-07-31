"""Deterministic durable render-preparation paths."""

from backend.src.production.rendering.ports import RenderStageContext


def local_render_request_relative_path(context: RenderStageContext) -> str:
    return (
        f"production/{context.job_id}/rendering/attempt-{context.attempt_number}/"
        "local-render-request.json"
    )


def render_execution_manifest_relative_path(context: RenderStageContext) -> str:
    return (
        f"production/{context.job_id}/rendering/attempt-{context.attempt_number}/"
        "render-execution-manifest.json"
    )


def ffmpeg_execution_plan_relative_path(context: RenderStageContext) -> str:
    return (
        f"production/{context.job_id}/rendering/attempt-{context.attempt_number}/"
        "ffmpeg-execution-plan.json"
    )


def ffmpeg_work_relative_path(
    context: RenderStageContext,
    request_fingerprint: str,
) -> str:
    return (
        f"production/{context.job_id}/rendering/attempt-{context.attempt_number}/"
        f"work/{request_fingerprint[:12]}"
    )
