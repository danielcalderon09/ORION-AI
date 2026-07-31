"""Non-rendering local validator for deterministic preparation."""

from __future__ import annotations

from backend.src.production.rendering.exceptions import RenderingValidationError
from backend.src.production.rendering.models import (
    DryRunRenderResult,
    LocalRenderRequest,
    RendererActivationState,
    RendererCapabilities,
    RendererDescription,
    RendererKind,
    RendererReadiness,
)
from backend.src.production.rendering.request_builder import (
    render_request_fingerprint,
)

DRY_RUN_RENDERER_VERSION = "1.0.0"


def _capabilities(
    kind: RendererKind,
    *,
    validates_planning_features: bool,
) -> RendererCapabilities:
    return RendererCapabilities(
        renderer_kind=kind,
        renderer_version=DRY_RUN_RENDERER_VERSION,
        produces_media=False,
        supports_video_tracks=validates_planning_features,
        supports_narration=validates_planning_features,
        supports_music=validates_planning_features,
        supports_sound_effects=validates_planning_features,
        supports_subtitles=validates_planning_features,
        supports_transitions=validates_planning_features,
        supports_volume_envelopes=validates_planning_features,
        supports_ducking=validates_planning_features,
        supports_fades=validates_planning_features,
        supports_vertical_video=validates_planning_features,
        deterministic_preparation=True,
    )


def renderer_descriptions(
    active_renderer: RendererKind = RendererKind.DRY_RUN,
) -> tuple[RendererDescription, ...]:
    return (
        RendererDescription(
            renderer_kind=RendererKind.DRY_RUN,
            activation_state=(
                RendererActivationState.ACTIVE
                if active_renderer is RendererKind.DRY_RUN
                else RendererActivationState.DISABLED
            ),
            readiness=RendererReadiness.READY,
            capabilities=_capabilities(
                RendererKind.DRY_RUN,
                validates_planning_features=True,
            ),
        ),
        RendererDescription(
            renderer_kind=RendererKind.FFMPEG,
            activation_state=(
                RendererActivationState.ACTIVE
                if active_renderer is RendererKind.FFMPEG
                else RendererActivationState.DISABLED
            ),
            readiness=(
                RendererReadiness.READY
                if active_renderer is RendererKind.FFMPEG
                else RendererReadiness.NOT_CONFIGURED
            ),
            capabilities=RendererCapabilities(
                renderer_kind=RendererKind.FFMPEG,
                renderer_version="1.0.0",
                produces_media=True,
                supported_container_formats=("mp4",),
                supported_video_codecs=("h264",),
                supported_audio_codecs=("aac",),
                supports_video_tracks=True,
                supports_narration=True,
                supports_music=True,
                supports_sound_effects=True,
                supports_subtitles=True,
                supports_transitions=True,
                supports_volume_envelopes=True,
                supports_ducking=True,
                supports_fades=True,
                supports_vertical_video=True,
                max_width=16_384,
                max_height=16_384,
                max_frame_rate=120,
                deterministic_preparation=True,
            ),
        ),
        RendererDescription(
            renderer_kind=RendererKind.DAVINCI_RESOLVE,
            activation_state=RendererActivationState.DISABLED,
            readiness=RendererReadiness.NOT_CONFIGURED,
            capabilities=_capabilities(
                RendererKind.DAVINCI_RESOLVE,
                validates_planning_features=False,
            ),
        ),
    )


class DryRunRenderer:
    @property
    def renderer_kind(self) -> RendererKind:
        return RendererKind.DRY_RUN

    @property
    def capabilities(self) -> RendererCapabilities:
        return renderer_descriptions()[0].capabilities

    async def prepare_or_validate(
        self,
        request: LocalRenderRequest,
        execution_plan: object | None = None,
    ) -> DryRunRenderResult:
        if execution_plan is not None:
            raise RenderingValidationError("dry-run renderer received an execution plan")
        if request.renderer_kind is not self.renderer_kind or not request.dry_run:
            raise RenderingValidationError("dry-run renderer received another renderer request")
        if render_request_fingerprint(request) != request.request_fingerprint:
            raise RenderingValidationError("render request fingerprint differs")
        summary = request.track_summary
        if (
            request.expected_duration_ms <= 0
            or request.expected_duration_frames <= 0
            or request.output_width <= 0
            or request.output_height <= 0
            or request.frame_rate_numerator <= 0
            or request.frame_rate_denominator <= 0
        ):
            raise RenderingValidationError("render request timing or dimensions are invalid")
        if summary.track_count != 5 or not summary.has_video or not summary.has_narration:
            raise RenderingValidationError("required composition tracks are not represented")
        if request.asset_count != len(request.asset_fingerprints):
            raise RenderingValidationError("render request asset count differs")
        ids = tuple(item.asset_id for item in request.asset_fingerprints)
        if len(ids) != len(set(ids)) or ids != tuple(sorted(ids)):
            raise RenderingValidationError("render request assets are duplicated or unordered")
        if any(
            not item.sha256 or not item.fingerprint or "\\" in item.relative_path
            for item in request.asset_fingerprints
        ):
            raise RenderingValidationError("render request asset reference is invalid")
        if (
            request.requested_output.filename
            != request.requested_output.relative_path.split("/")[-1]
        ):
            raise RenderingValidationError("render output filename is unsafe")
        feature_checks = (
            (summary.has_video, self.capabilities.supports_video_tracks),
            (summary.has_narration, self.capabilities.supports_narration),
            (summary.has_music, self.capabilities.supports_music),
            (summary.has_sound_effects, self.capabilities.supports_sound_effects),
            (summary.has_subtitles, self.capabilities.supports_subtitles),
            (summary.has_transitions, self.capabilities.supports_transitions),
            (summary.has_volume_envelopes, self.capabilities.supports_volume_envelopes),
            (summary.has_ducking, self.capabilities.supports_ducking),
            (summary.has_fades, self.capabilities.supports_fades),
            (
                request.output_height > request.output_width,
                self.capabilities.supports_vertical_video,
            ),
        )
        if any(requested and not supported for requested, supported in feature_checks):
            raise RenderingValidationError("render request uses an unsupported planning feature")
        return DryRunRenderResult(
            renderer_version=DRY_RUN_RENDERER_VERSION,
            request_fingerprint=request.request_fingerprint,
            accepted=True,
            validated_asset_count=request.asset_count,
            validated_track_count=summary.track_count,
            validation_codes=(
                "asset_references_valid",
                "output_contract_valid",
                "plan_identity_valid",
                "timeline_features_supported",
                "track_summary_valid",
            ),
            metadata={
                "real_renderer_executed": False,
                "validation_only": True,
            },
        )

    async def close(self) -> None:
        return None
