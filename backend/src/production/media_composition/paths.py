"""Contractual durable paths for composition outputs."""

from backend.src.production.media_composition.ports import (
    MediaCompositionStageContext,
)


def media_composition_plan_relative_path(
    context: MediaCompositionStageContext,
) -> str:
    return (
        f"production/{context.job_id}/building_timeline/"
        f"attempt-{context.attempt_number}/media-composition-plan.json"
    )


def media_composition_manifest_relative_path(
    context: MediaCompositionStageContext,
) -> str:
    return (
        f"production/{context.job_id}/building_timeline/"
        f"attempt-{context.attempt_number}/media-composition-manifest.json"
    )
