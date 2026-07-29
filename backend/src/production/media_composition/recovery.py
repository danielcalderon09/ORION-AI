"""Pure recovery classification and manifest projection."""

from datetime import datetime

from backend.src.production.media_composition.domain.models import (
    CompositionAssetAvailability,
    CompositionIssueSeverity,
    CompositionManifestStatus,
    CompositionValidationIssue,
    MediaCompositionManifest,
    MediaCompositionPlan,
    TimelineValidationSummary,
)
from backend.src.production.media_composition.ports import MediaCompositionSource


def project_manifest(
    *,
    plan: MediaCompositionPlan,
    source: MediaCompositionSource,
    attempt_number: int,
    plan_relative_path: str,
    plan_sha256: str,
    plan_size_bytes: int,
    now: datetime,
    existing: MediaCompositionManifest | None,
) -> MediaCompositionManifest:
    missing = sum(
        item.availability is CompositionAssetAvailability.MISSING
        for item in source.asset_validation
    )
    corrupt = sum(
        item.availability is CompositionAssetAvailability.CORRUPT
        for item in source.asset_validation
    )
    issues = [
        CompositionValidationIssue(
            code=item.issue_code or "asset_unavailable",
            severity=CompositionIssueSeverity.ERROR,
            asset_id=item.asset_id,
        )
        for item in source.asset_validation
        if item.availability is not CompositionAssetAvailability.AVAILABLE
    ]
    issues.extend(
        CompositionValidationIssue(
            code="orphan_asset",
            severity=CompositionIssueSeverity.WARNING,
            asset_id=asset_id,
        )
        for asset_id in source.orphan_asset_ids
    )
    looped_video = tuple(clip for clip in plan.tracks[0].clips if clip.playback_mode == "loop")
    issues.extend(
        CompositionValidationIssue(
            code="source_duration_looped",
            severity=CompositionIssueSeverity.WARNING,
            asset_id=clip.asset_id,
            track_id="track-video",
        )
        for clip in looped_video
    )
    status = (
        CompositionManifestStatus.COMPLETE
        if not missing and not corrupt
        else CompositionManifestStatus.INVALIDATED
    )
    generated_at = existing.generated_at if existing is not None else now
    projected = MediaCompositionManifest(
        job_id=plan.job_id,
        attempt_number=attempt_number,
        source_fingerprint=plan.source_fingerprint,
        plan_fingerprint=plan.plan_fingerprint,
        timeline_checksum=plan.timeline_checksum,
        plan_relative_path=plan_relative_path,
        plan_sha256=plan_sha256,
        plan_size_bytes=plan_size_bytes,
        asset_inventory=source.asset_validation,
        validation_summary=TimelineValidationSummary(
            gaps=0,
            overlaps=0,
            missing_assets=missing,
            corrupt_assets=corrupt,
            duplicate_assets=0,
            duration_mismatches=len(looped_video),
            frame_inconsistencies=0,
            orphan_assets=len(source.orphan_asset_ids),
            errors=missing + corrupt,
            warnings=len(source.orphan_asset_ids) + len(looped_video),
        ),
        issues=tuple(issues),
        status=status,
        generated_at=generated_at,
        updated_at=now,
        metadata={
            "content_generation": False,
            "partial_invalidation": True,
            "renderer_execution": False,
        },
    )
    if existing is not None and projected.model_dump(exclude={"updated_at"}) == existing.model_dump(
        exclude={"updated_at"}
    ):
        return existing
    return projected
