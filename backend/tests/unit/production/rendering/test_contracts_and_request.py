"""Renderer identities, capabilities, request derivation, and dry-run validation."""

from pathlib import Path

import pytest

from backend.src.production.media_composition.configuration import (
    MediaCompositionConfiguration,
)
from backend.src.production.media_composition.domain.fingerprints import canonical_sha256
from backend.src.production.media_composition.domain.models import (
    CompositionAssetAvailability,
    CompositionAssetKind,
    CompositionAssetReference,
    CompositionAssetValidation,
)
from backend.src.production.media_composition.ports import CompositionSubtitleSource
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.exceptions import (
    RenderingRequestError,
    RenderingValidationError,
)
from backend.src.production.rendering.models import (
    RendererActivationState,
    RendererKind,
    RendererReadiness,
)
from backend.src.production.rendering.recovery import capabilities_fingerprint
from backend.src.production.rendering.renderers import (
    DryRunRenderer,
    renderer_descriptions,
)
from backend.src.production.rendering.request_builder import (
    build_local_render_request,
)
from backend.tests.unit.production.media_composition.conftest import (
    JOB_ID,
    make_source,
)
from backend.tests.unit.production.rendering.conftest import make_verified_source


def test_renderer_vocabulary_is_closed_and_only_dry_run_is_active() -> None:
    assert tuple(RendererKind) == (
        RendererKind.DRY_RUN,
        RendererKind.FFMPEG,
        RendererKind.DAVINCI_RESOLVE,
    )
    descriptions = renderer_descriptions()
    assert descriptions[0].activation_state is RendererActivationState.ACTIVE
    assert descriptions[0].readiness is RendererReadiness.READY
    assert all(
        item.activation_state is RendererActivationState.DISABLED
        and item.readiness is RendererReadiness.NOT_CONFIGURED
        for item in descriptions[1:]
    )
    with pytest.raises(ValueError):
        RendererKind("third_party")


def test_capabilities_are_stable_and_conservative() -> None:
    first = renderer_descriptions()
    second = renderer_descriptions()
    assert first == second
    assert first[0].capabilities.produces_media is False
    assert first[0].capabilities.supported_container_formats == ()
    assert first[0].capabilities.supported_video_codecs == ()
    assert first[0].capabilities.supported_audio_codecs == ()
    assert capabilities_fingerprint(first[0].capabilities) == capabilities_fingerprint(
        second[0].capabilities
    )
    assert all(not item.capabilities.supports_video_tracks for item in first[1:])


def test_request_is_deterministic_safe_and_attempt_independent() -> None:
    source = make_verified_source()
    first = build_local_render_request(source, RenderingConfiguration())
    second = build_local_render_request(source, RenderingConfiguration())
    assert first == second
    assert first.request_id == second.request_id
    assert first.request_fingerprint == second.request_fingerprint
    assert first.requested_output.filename == (
        f"orion-{first.job_id}-{first.source_plan_fingerprint[:12]}.mp4"
    )
    assert first.requested_output.relative_path == (
        f"production/{first.job_id}/output/{first.requested_output.filename}"
    )
    assert first.requested_output.include_subtitles is False
    assert first.track_summary.video_clip_count == 2
    assert first.track_summary.narration_clip_count == 1
    assert tuple(item.asset_id for item in first.asset_fingerprints) == tuple(
        sorted(item.asset_id for item in first.asset_fingerprints)
    )
    content = first.model_dump_json()
    assert "C:\\Users\\operator" not in content
    assert "operator" not in content
    assert "attempt_number" not in content
    assert "generated_at" not in content


def test_request_changes_only_with_logical_plan_identity() -> None:
    first = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    changed = build_local_render_request(
        make_verified_source(MediaCompositionConfiguration(fade_duration_ms=300)),
        RenderingConfiguration(),
    )
    assert first.request_fingerprint != changed.request_fingerprint
    assert first.request_id != changed.request_id


def test_requested_subtitles_follow_composition_plan() -> None:
    source = make_source()
    digest = canonical_sha256({"asset_id": "subtitles-main"})
    subtitle_asset = CompositionAssetReference(
        asset_id="subtitles-main",
        artifact_id="30000000-0000-4000-8000-000000000999",
        kind=CompositionAssetKind.SUBTITLES,
        relative_path=f"production/{JOB_ID}/assets/subtitles/subtitles-main.srt",
        mime_type="application/x-subrip",
        sha256=digest,
        fingerprint=canonical_sha256({"fingerprint": "subtitles-main"}),
        size_bytes=128,
        duration_ms=4_000,
    )
    assets = tuple(sorted(source.assets + (subtitle_asset,), key=lambda item: item.asset_id))
    validation = tuple(
        CompositionAssetValidation(
            asset_id=item.asset_id,
            availability=CompositionAssetAvailability.AVAILABLE,
            relative_path=item.relative_path,
            expected_sha256=item.sha256,
            actual_sha256=item.sha256,
        )
        for item in assets
    )
    with_subtitles = source.model_copy(
        update={
            "assets": assets,
            "asset_validation": validation,
            "subtitles": CompositionSubtitleSource(
                asset_id=subtitle_asset.asset_id,
                cue_start_ms=(0,),
                cue_end_ms=(1_000,),
                cue_text_sha256=(canonical_sha256({"text": "caption"}),),
            ),
        }
    )
    request = build_local_render_request(
        make_verified_source(composition_source=with_subtitles),
        RenderingConfiguration(),
    )
    assert request.requested_output.include_subtitles is True
    assert request.track_summary.has_subtitles is True
    assert request.track_summary.subtitle_cue_count == 1


def test_request_rejects_duplicate_assets_and_unsupported_schema() -> None:
    source = make_verified_source()
    duplicate_plan = source.plan.model_copy(
        update={"assets": source.plan.assets + (source.plan.assets[0],)}
    )
    duplicate_source = type(source)(
        plan_artifact=source.plan_artifact,
        manifest_artifact=source.manifest_artifact,
        plan=duplicate_plan,
        manifest=source.manifest,
    )
    with pytest.raises(RenderingRequestError, match="duplicate"):
        build_local_render_request(duplicate_source, RenderingConfiguration())
    unsupported_plan = source.plan.model_copy(update={"schema_version": "2.0.0"})
    unsupported_source = type(source)(
        plan_artifact=source.plan_artifact,
        manifest_artifact=source.manifest_artifact,
        plan=unsupported_plan,
        manifest=source.manifest,
    )
    with pytest.raises(RenderingRequestError, match="schema"):
        build_local_render_request(unsupported_source, RenderingConfiguration())


@pytest.mark.parametrize(
    "field",
    ["width", "frame_rate_numerator"],
)
def test_request_rejects_invalid_output_geometry_or_rate(field: str) -> None:
    source = make_verified_source()
    invalid_output = source.plan.output.model_copy(update={field: 0})
    invalid_plan = source.plan.model_copy(update={"output": invalid_output})
    invalid_source = type(source)(
        plan_artifact=source.plan_artifact,
        manifest_artifact=source.manifest_artifact,
        plan=invalid_plan,
        manifest=source.manifest,
    )
    with pytest.raises(RenderingRequestError):
        build_local_render_request(invalid_source, RenderingConfiguration())


def test_request_rejects_missing_asset_checksum() -> None:
    source = make_verified_source()
    invalid_asset = source.plan.assets[0].model_copy(update={"sha256": None})
    invalid_plan = source.plan.model_copy(
        update={"assets": (invalid_asset,) + source.plan.assets[1:]}
    )
    invalid_source = type(source)(
        plan_artifact=source.plan_artifact,
        manifest_artifact=source.manifest_artifact,
        plan=invalid_plan,
        manifest=source.manifest,
    )
    with pytest.raises(ValueError):
        build_local_render_request(invalid_source, RenderingConfiguration())


@pytest.mark.asyncio
async def test_dry_run_accepts_without_creating_output_or_directory(
    tmp_path: Path,
) -> None:
    request = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    before = tuple(tmp_path.rglob("*"))
    first = await DryRunRenderer().prepare_or_validate(request)
    second = await DryRunRenderer().prepare_or_validate(request)
    assert first == second
    assert first.accepted is True
    assert first.media_produced is False
    assert first.output_created is False
    assert first.validated_asset_count == request.asset_count
    assert first.validated_track_count == request.track_summary.track_count
    assert tuple(tmp_path.rglob("*")) == before == ()


@pytest.mark.asyncio
async def test_dry_run_rejects_another_renderer_identity() -> None:
    request = build_local_render_request(
        make_verified_source(),
        RenderingConfiguration(),
    )
    invalid = request.model_copy(update={"renderer_kind": RendererKind.FFMPEG})
    with pytest.raises(RenderingValidationError, match="another renderer"):
        await DryRunRenderer().prepare_or_validate(invalid)
