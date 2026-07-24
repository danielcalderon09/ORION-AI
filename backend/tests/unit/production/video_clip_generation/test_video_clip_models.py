"""Strict video clip contracts and serialization."""

from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipEntry,
    ProductionVideoClipManifest,
    VideoClipEntryStatus,
    VideoClipManifestStatus,
    replace_manifest_entry,
    summarize_entries,
)
from backend.src.production.video_clip_generation.ports import (
    GeneratedVideoClipPayload,
)
from backend.src.production.video_clip_generation.serialization import (
    deserialize_video_clip_manifest,
    serialize_video_clip_manifest,
)
from backend.src.production.visual_asset_planning.models import VisualAssetRole
from backend.tests.unit.production.video_clip_generation.conftest import (
    IMAGE_ARTIFACT_ID,
    MANIFEST_ID,
    VISUAL_ASSET_ID,
)


def entry(status=VideoClipEntryStatus.PENDING, **changes):
    values = {
        "visual_asset_id": VISUAL_ASSET_ID,
        "source_image_artifact_id": IMAGE_ARTIFACT_ID,
        "source_image_binary_asset_id": f"image-{VISUAL_ASSET_ID}",
        "source_image_sha256": "a" * 64,
        "source_scene_id": "scene-001",
        "source_shot_id": "scene-001-shot-001",
        "scene_number": 1,
        "shot_number": 1,
        "role": VisualAssetRole.PRIMARY,
        "status": status,
        "attempt_number": 1,
    }
    values.update(changes)
    return ProductionVideoClipEntry(**values)


def manifest(item=None, status=VideoClipManifestStatus.IN_PROGRESS):
    entries = (item or entry(),)
    return ProductionVideoClipManifest(
        source_image_manifest_schema_version="1.0.0",
        source_image_manifest_artifact_id=MANIFEST_ID,
        source_image_manifest_sha256="b" * 64,
        provider="simulated",
        requested_model="simulated-video-v1",
        configuration_fingerprint="c" * 64,
        entries=entries,
        summary=summarize_entries(entries),
        status=status,
    )


def test_contracts_are_frozen_extra_forbid_and_versioned() -> None:
    value = manifest()
    with pytest.raises(ValidationError):
        value.status = VideoClipManifestStatus.FAILED
    with pytest.raises(ValidationError):
        ProductionVideoClipManifest(
            **value.model_dump(),
            unknown=True,
        )
    assert value.schema_version == "1.0.0"


def test_entries_must_be_unique_sorted_and_scene_mapped() -> None:
    duplicate = (entry(), entry())
    with pytest.raises(ValidationError):
        ProductionVideoClipManifest(
            **{
                **manifest().model_dump(),
                "entries": duplicate,
                "summary": summarize_entries(duplicate),
            }
        )
    with pytest.raises(ValidationError):
        entry(source_shot_id="scene-002-shot-001")


def test_transitions_allow_pending_generating_stored_only_forward() -> None:
    pending = manifest()
    generating_entry = entry(status=VideoClipEntryStatus.GENERATING)
    generating = replace_manifest_entry(pending, generating_entry)
    stored_entry = entry(
        status=VideoClipEntryStatus.STORED,
        video_binary_asset_id=f"video-{VISUAL_ASSET_ID}",
        video_artifact_id=UUID(int=9),
        storage_path=f"production/{UUID(int=1)}/assets/video-clips/video-{VISUAL_ASSET_ID}.mp4",
        mime_type="video/mp4",
        extension="mp4",
        sha256="d" * 64,
        size_bytes=100,
        width=64,
        height=64,
        duration_seconds=1,
        frame_rate=24,
        frame_count=24,
        video_codec="h264",
        has_audio=False,
        provider="simulated",
    )
    stored = replace_manifest_entry(generating, stored_entry)
    from backend.src.production.video_clip_generation.models import (
        validate_manifest_transition,
    )

    validate_manifest_transition(pending, generating)
    validate_manifest_transition(generating, stored)
    with pytest.raises(ValueError):
        validate_manifest_transition(stored, pending)
    uncertain = replace_manifest_entry(
        generating,
        entry(
            status=VideoClipEntryStatus.UNCERTAIN,
            error_code="interrupted",
        ),
        status=VideoClipManifestStatus.UNCERTAIN,
    )
    with pytest.raises(ValueError):
        validate_manifest_transition(uncertain, generating)


def test_completed_manifest_requires_every_entry_stored() -> None:
    with pytest.raises(ValidationError):
        manifest(status=VideoClipManifestStatus.COMPLETED)


def test_bytes_are_excluded_from_repr_and_serialization() -> None:
    payload = GeneratedVideoClipPayload(
        content=b"secret-video-bytes",
        mime_type="video/mp4",
        index=0,
    )
    assert "secret-video-bytes" not in repr(payload)
    assert "content" not in payload.model_dump()


@pytest.mark.parametrize(
    "metadata",
    [
        {"api_key": "secret"},
        {"password": "secret"},
        {"token": "secret"},
        {"value": float("nan")},
    ],
)
def test_unsafe_metadata_is_rejected(metadata) -> None:
    with pytest.raises(ValidationError):
        entry(metadata=metadata)


def test_decimal_and_canonical_json_are_safe() -> None:
    failed = entry(
        status=VideoClipEntryStatus.FAILED_TRANSIENT,
        error_code="timeout",
        cost_usd=Decimal("0.000000000"),
    )
    value = manifest(failed, status=VideoClipManifestStatus.FAILED)
    first = serialize_video_clip_manifest(value)
    second = serialize_video_clip_manifest(value)
    assert first == second
    assert deserialize_video_clip_manifest(first) == value
    assert b"NaN" not in first


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "remote"},
        {"output_format": "webm"},
        {"codec": "vp9"},
        {"frame_rate": 25},
        {"duration_seconds": 0},
        {"duration_seconds": 11},
    ],
)
def test_configuration_is_closed(overrides) -> None:
    with pytest.raises(ValidationError):
        VideoClipGenerationConfiguration(**overrides)


def test_configuration_fingerprint_is_stable_and_sensitive() -> None:
    first = VideoClipGenerationConfiguration(duration_seconds=1)
    assert first.fingerprint() == VideoClipGenerationConfiguration(
        duration_seconds=1
    ).fingerprint()
    assert first.fingerprint() != VideoClipGenerationConfiguration(
        duration_seconds=2
    ).fingerprint()
