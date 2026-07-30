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
