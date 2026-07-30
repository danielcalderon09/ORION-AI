"""Derive one deterministic local render request from a verified plan."""

from uuid import NAMESPACE_URL, uuid5

from backend.src.production.media_composition.domain.models import (
    SUPPORTED_MEDIA_COMPOSITION_VERSIONS,
)
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.exceptions import RenderingRequestError
from backend.src.production.rendering.fingerprints import canonical_sha256
from backend.src.production.rendering.models import (
    LOCAL_RENDER_SCHEMA_VERSION,
    RENDERER_CONTRACT_VERSION,
    LocalRenderRequest,
    RenderAssetReference,
    RendererKind,
    RenderTrackSummary,
    RequestedRenderOutput,
)
from backend.src.production.rendering.ports import VerifiedCompositionSource


def build_local_render_request(
    source: VerifiedCompositionSource,
    configuration: RenderingConfiguration,
) -> LocalRenderRequest:
    if configuration.renderer is not RendererKind.DRY_RUN:
        raise RenderingRequestError("only dry_run rendering is active")
    plan = source.plan
    if plan.schema_version not in SUPPORTED_MEDIA_COMPOSITION_VERSIONS:
        raise RenderingRequestError("composition plan schema is unsupported")
    if plan.job_id != source.plan_artifact.job_id:
        raise RenderingRequestError("composition plan belongs to another job")
    assets = tuple(
        RenderAssetReference(
            asset_id=item.asset_id,
            relative_path=item.relative_path,
            sha256=item.sha256,
            fingerprint=item.fingerprint,
            media_kind=item.kind,
            duration_ms=item.duration_ms,
        )
        for item in sorted(plan.assets, key=lambda value: value.asset_id)
    )
    if len({item.asset_id for item in assets}) != len(assets):
        raise RenderingRequestError("composition plan contains duplicate asset identities")
    track_summary = _track_summary(plan)
    filename = f"orion-{plan.job_id}-{plan.plan_fingerprint[:12]}.mp4"
    requested_output = RequestedRenderOutput(
        container_format=configuration.output_container,
        video_codec=configuration.video_codec,
        audio_codec=configuration.audio_codec,
        filename=filename,
        relative_path=f"production/{plan.job_id}/output/{filename}",
        pixel_format=configuration.pixel_format,
        include_subtitles=track_summary.has_subtitles,
    )
    fingerprint_payload = {
        "asset_fingerprints": [
            {"asset_id": item.asset_id, "fingerprint": item.fingerprint} for item in assets
        ],
        "dry_run": True,
        "duration": {
            "frames": plan.output.expected_duration_frames,
            "milliseconds": plan.output.expected_duration_ms,
        },
        "frame_rate": {
            "denominator": plan.output.frame_rate_denominator,
            "numerator": plan.output.frame_rate_numerator,
        },
        "output_dimensions": {
            "height": plan.output.height,
            "width": plan.output.width,
        },
        "renderer_contract_version": RENDERER_CONTRACT_VERSION,
        "renderer_kind": RendererKind.DRY_RUN.value,
        "requested_output": requested_output.model_dump(mode="json"),
        "schema_version": LOCAL_RENDER_SCHEMA_VERSION,
        "source_plan_fingerprint": plan.plan_fingerprint,
        "source_plan_sha256": source.plan_artifact.sha256,
        "timeline_checksum": plan.timeline_checksum,
        "track_summary": track_summary.model_dump(mode="json"),
    }
    if source.plan_artifact.sha256 is None:
        raise RenderingRequestError("composition plan artifact has no checksum")
    fingerprint = canonical_sha256(fingerprint_payload)
    try:
        return LocalRenderRequest(
            request_id=uuid5(NAMESPACE_URL, f"orion:local-render-request:{fingerprint}"),
            job_id=plan.job_id,
            source_plan_artifact_id=source.plan_artifact.artifact_id,
            source_plan_relative_path=source.plan_artifact.relative_path,
            source_plan_sha256=source.plan_artifact.sha256,
            source_plan_fingerprint=plan.plan_fingerprint,
            timeline_checksum=plan.timeline_checksum,
            expected_duration_ms=plan.output.expected_duration_ms,
            expected_duration_frames=plan.output.expected_duration_frames,
            output_width=plan.output.width,
            output_height=plan.output.height,
            frame_rate_numerator=plan.output.frame_rate_numerator,
            frame_rate_denominator=plan.output.frame_rate_denominator,
            aspect_ratio=plan.output.aspect_ratio,
            color_space=plan.output.color_space,
            track_summary=track_summary,
            asset_count=len(assets),
            asset_fingerprints=assets,
            requested_output=requested_output,
            request_fingerprint=fingerprint,
            metadata={
                "authoritative_source": "media-composition-plan.json",
                "renderer_execution": False,
            },
        )
    except ValueError as exc:
        raise RenderingRequestError("composition plan cannot form a render request") from exc


def render_request_fingerprint(request: LocalRenderRequest) -> str:
    return canonical_sha256(
        {
            "asset_fingerprints": [
                {"asset_id": item.asset_id, "fingerprint": item.fingerprint}
                for item in request.asset_fingerprints
            ],
            "dry_run": request.dry_run,
            "duration": {
                "frames": request.expected_duration_frames,
                "milliseconds": request.expected_duration_ms,
            },
            "frame_rate": {
                "denominator": request.frame_rate_denominator,
                "numerator": request.frame_rate_numerator,
            },
            "output_dimensions": {
                "height": request.output_height,
                "width": request.output_width,
            },
            "renderer_contract_version": RENDERER_CONTRACT_VERSION,
            "renderer_kind": request.renderer_kind.value,
            "requested_output": request.requested_output.model_dump(mode="json"),
            "schema_version": request.schema_version,
            "source_plan_fingerprint": request.source_plan_fingerprint,
            "source_plan_sha256": request.source_plan_sha256,
            "timeline_checksum": request.timeline_checksum,
            "track_summary": request.track_summary.model_dump(mode="json"),
        }
    )


def _track_summary(plan: object) -> RenderTrackSummary:
    from backend.src.production.media_composition.domain.models import MediaCompositionPlan

    if not isinstance(plan, MediaCompositionPlan):
        raise RenderingRequestError("render source is not a media composition plan")
    counts = {track.kind.value: len(track.clips) for track in plan.tracks}
    clips = tuple(clip for track in plan.tracks for clip in track.clips)
    fade_count = sum(bool(clip.fade_in_ms or clip.fade_out_ms) for clip in clips)
    envelope_count = sum(clip.volume_envelope is not None for clip in clips)
    return RenderTrackSummary(
        track_count=len(plan.tracks),
        enabled_track_count=sum(track.enabled for track in plan.tracks),
        clip_count=len(clips),
        video_clip_count=counts["video"],
        narration_clip_count=counts["narration"],
        music_clip_count=counts["music"],
        sound_effect_clip_count=counts["sound_effect"],
        subtitle_clip_count=counts["subtitles"],
        transition_count=len(plan.transitions),
        subtitle_cue_count=len(plan.subtitle_cues),
        volume_envelope_count=envelope_count,
        ducking_instruction_count=len(plan.ducking),
        fade_clip_count=fade_count,
        has_video=bool(counts["video"]),
        has_narration=bool(counts["narration"]),
        has_music=bool(counts["music"]),
        has_sound_effects=bool(counts["sound_effect"]),
        has_subtitles=bool(counts["subtitles"] or plan.subtitle_cues),
        has_transitions=bool(plan.transitions),
        has_volume_envelopes=bool(envelope_count),
        has_ducking=bool(plan.ducking),
        has_fades=bool(fade_count),
    )
