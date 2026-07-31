"""Deterministic durable final-render validation paths."""

from backend.src.production.render_validation.ports import FinalValidationStageContext


def final_render_validation_relative_path(context: FinalValidationStageContext) -> str:
    return (
        f"production/{context.job_id}/validating_render/attempt-{context.attempt_number}/"
        "final-render-validation.json"
    )
