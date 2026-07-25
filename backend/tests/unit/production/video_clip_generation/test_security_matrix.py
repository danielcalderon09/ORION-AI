"""Closed-input security regression matrix for Phase 5F.1."""

import pytest
from pydantic import ValidationError

from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.exceptions import (
    VideoClipIntegrityError,
)
from backend.src.production.video_clip_generation.media_probe import (
    FFprobeMediaProbe,
    VideoClipIntegrityValidator,
)
from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipEntry,
    VideoClipEntryStatus,
)
from backend.src.production.video_clip_generation.serialization import (
    deserialize_video_clip_manifest,
)
from backend.src.production.visual_asset_planning.models import VisualAssetRole
from backend.tests.unit.production.video_clip_generation.conftest import (
    IMAGE_ARTIFACT_ID,
    VISUAL_ASSET_ID,
)

_INVALID_CONFIGURATIONS = (
    [
        {"provider": value}
        for value in (
            "veo",
            "kling",
            "runway",
            "luma",
            "pika",
            "replicate",
            "fal",
            "remote",
            "",
        )
    ]
    + [{"output_format": value} for value in ("webm", "mov", "avi", "gif", "mpegts")]
    + [{"codec": value} for value in ("vp8", "vp9", "av1", "hevc", "mpeg4")]
    + [{"frame_rate": value} for value in (0, 1, 23, 25, 29, 31)]
    + [{"duration_seconds": value} for value in (-10, -1, 0, 10.1)]
)


@pytest.mark.parametrize("overrides", _INVALID_CONFIGURATIONS)
def test_remote_formats_codecs_and_unbounded_media_are_closed(overrides) -> None:
    with pytest.raises(ValidationError):
        VideoClipGenerationConfiguration(**overrides)


_UNSAFE_METADATA = (
    [
        {key: "sensitive"}
        for key in (
            "api_key",
            "provider_api_key",
            "authorization",
            "authorization_header",
            "credential",
            "provider_credential",
            "http_referer",
            "password",
            "db_password",
            "secret",
            "client_secret",
            "token",
            "access_token",
            "refresh_token",
            "x-openrouter-title",
            "x_title",
        )
    ]
    + [
        {"path": value}
        for value in (
            "C:\\Users\\operator\\clip.mp4",
            "D:\\temp\\video.mp4",
            "/home/operator/video.mp4",
            "/tmp/video.mp4",
            "\\\\server\\share\\video.mp4",
        )
    ]
    + [
        {"nested": {key: "sensitive"}}
        for key in (
            "api_key",
            "authorization",
            "credential",
            "password",
            "secret",
            "token",
            "http_referer",
            "x_title",
            "access_token",
        )
    ]
)


@pytest.mark.parametrize("metadata", _UNSAFE_METADATA)
def test_manifest_entry_metadata_rejects_secrets_and_absolute_paths(
    metadata,
) -> None:
    with pytest.raises(ValidationError):
        ProductionVideoClipEntry(
            visual_asset_id=VISUAL_ASSET_ID,
            source_image_artifact_id=IMAGE_ARTIFACT_ID,
            source_image_binary_asset_id=f"image-{VISUAL_ASSET_ID}",
            source_image_sha256="a" * 64,
            source_scene_id="scene-001",
            source_shot_id="scene-001-shot-001",
            scene_number=1,
            shot_number=1,
            role=VisualAssetRole.PRIMARY,
            status=VideoClipEntryStatus.PENDING,
            attempt_number=1,
            metadata=metadata,
        )


_INVALID_BINARY_PAYLOADS = [
    b"",
    b"x",
    b"\x00" * 8,
    b"<html></html>",
    b"<?xml version='1.0'?>",
    b"<svg></svg>",
    b"MZ" + b"\x00" * 20,
    b"\x7fELF" + b"\x00" * 20,
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
    b"\xff\xd8\xff" + b"\x00" * 20,
    b"RIFF\x04\x00\x00\x00WEBP",
    b"GIF89a" + b"\x00" * 20,
    b"PK\x03\x04" + b"\x00" * 20,
    b"{" + b'"video":"base64"}',
    b"[" + b'"video"]',
    b"\x00\x00\x00\x18moov" + b"\x00" * 20,
    b"\x00\x00\x00\x18mdat" + b"\x00" * 20,
    b"\x00\x00\x00\x18free" + b"\x00" * 20,
    b"\x00\x00\x00\x18skip" + b"\x00" * 20,
    b"\x00\x00\x00\x18wide" + b"\x00" * 20,
    b"not-a-video" * 4,
    b"\x00\x00\x00\x00ftyp",
    b"\x00\x00\x00\x08ftyp",
    b"    <script>alert(1)</script>",
    b"\xef\xbb\xbf<html></html>",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("content", _INVALID_BINARY_PAYLOADS)
async def test_integrity_validator_rejects_non_mp4_payload_classes(content) -> None:
    validator = VideoClipIntegrityValidator(
        probe=FFprobeMediaProbe(),
        max_video_bytes=1_000_000,
    )
    with pytest.raises(VideoClipIntegrityError):
        await validator.validate_content(
            content,
            expected_width=64,
            expected_height=64,
            expected_duration_seconds=1,
            expected_frame_rate=24,
        )


_INVALID_JSON = [
    b"",
    b" ",
    b"null",
    b"[]",
    b"{}",
    b"{",
    b"[",
    b'{"schema_version":NaN}',
    b'{"schema_version":Infinity}',
    b'{"schema_version":-Infinity}',
    b'{"schema_version":"1.0.0","schema_version":"1.0.0"}',
    b"\xff\xfe\x00\x00",
    b'{"schema_version": "9.9.9"}',
    b'{"entries": []}',
    b'{"status": "completed"}',
]


@pytest.mark.parametrize("content", _INVALID_JSON)
def test_manifest_deserializer_rejects_noncanonical_or_invalid_json(content) -> None:
    with pytest.raises((ValidationError, UnicodeError, ValueError, TypeError)):
        deserialize_video_clip_manifest(content)
