"""Durable remote jobs, settings, manifests, and fail-closed composition."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from backend.src.production.composition.container import (
    build_production_container,
)
from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoConfigurationError,
    OpenRouterVideoInvalidResponseError,
    RemoteVideoJobConflictError,
    RemoteVideoJobStoreError,
    VideoFramePublicationUnavailableError,
)
from backend.src.production.video_clip_generation.models import (
    ProductionVideoClipEntry,
    VideoClipEntryStatus,
    VideoClipRemoteStatus,
)
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    OpenRouterRemoteStatus,
    OpenRouterVideoProviderConfiguration,
    RemoteVideoJobRecord,
)
from backend.src.production.video_clip_generation.providers.openrouter_provider import (
    _validate_base_url,
    _validate_client,
    _validated_remote_path,
)
from backend.src.production.video_clip_generation.reconciliation import (
    _contains_sensitive_remote_metadata,
    _remote_matches_entry,
)
from backend.src.production.video_clip_generation.remote_job_store import (
    InMemoryRemoteVideoJobStore,
    LocalRemoteVideoJobStore,
)
from backend.src.production.video_clip_generation.serialization import (
    deserialize_remote_video_job,
    serialize_remote_video_job,
)
from backend.src.production.visual_asset_planning.models import VisualAssetRole
from backend.tests.unit.production.video_clip_generation.conftest import (
    IMAGE_ARTIFACT_ID,
    JOB_ID,
    NOW,
    VISUAL_ASSET_ID,
)
from backend.tests.unit.production.video_clip_generation.test_integration import (
    settings as base_settings,
)


def record(**updates: object) -> RemoteVideoJobRecord:
    values: dict[str, object] = {
        "job_id": str(JOB_ID),
        "attempt_number": 1,
        "visual_asset_id": VISUAL_ASSET_ID,
        "model": "test/video-model",
        "source_image_sha256": "a" * 64,
        "prompt_sha256": "b" * 64,
        "capability_snapshot_hash": "c" * 64,
        "provider_request_fingerprint": "d" * 64,
        "publication_provider": "test",
        "publication_id": "publication-1",
        "publication_expires_at": NOW + timedelta(minutes=10),
        "remote_job_id": "job-1",
        "remote_generation_id": "generation-1",
        "remote_status": "pending",
        "submitted_at": NOW,
        "estimated_cost_usd": "0.04",
        "pricing_snapshot_at": NOW,
        "pricing_sku": "per-video-second",
        "safe_remote_path": "/api/v1/videos/job-1",
    }
    values.update(updates)
    return RemoteVideoJobRecord.model_validate(values)


def openrouter_entry(**updates: object) -> ProductionVideoClipEntry:
    values: dict[str, object] = {
        "visual_asset_id": VISUAL_ASSET_ID,
        "source_image_artifact_id": IMAGE_ARTIFACT_ID,
        "source_image_binary_asset_id": f"image-{VISUAL_ASSET_ID}",
        "source_image_sha256": "a" * 64,
        "source_scene_id": "scene-001",
        "source_shot_id": "scene-001-shot-001",
        "scene_number": 1,
        "shot_number": 1,
        "role": VisualAssetRole.PRIMARY,
        "status": VideoClipEntryStatus.STORED,
        "video_binary_asset_id": f"video-{VISUAL_ASSET_ID}",
        "video_artifact_id": IMAGE_ARTIFACT_ID,
        "storage_path": (f"production/{JOB_ID}/assets/video-clips/video-{VISUAL_ASSET_ID}.mp4"),
        "mime_type": "video/mp4",
        "extension": "mp4",
        "sha256": "e" * 64,
        "size_bytes": 100,
        "width": 720,
        "height": 720,
        "duration_seconds": 4,
        "frame_rate": 24,
        "frame_count": 96,
        "video_codec": "h264",
        "has_audio": False,
        "provider": "openrouter",
        "requested_model": "test/video-model",
        "reported_model": "test/video-model",
        "attempt_number": 1,
        "remote_provider": "openrouter",
        "remote_job_id": "job-1",
        "remote_generation_id": "generation-1",
        "remote_status": "completed",
        "remote_submitted_at": NOW,
        "remote_last_polled_at": NOW,
        "remote_poll_attempts": 2,
        "remote_terminal_at": NOW,
        "remote_content_available": True,
        "estimated_cost_usd": "0.04",
        "reported_cost_usd": "0.04",
        "pricing_snapshot_at": NOW,
        "pricing_sku": "per-video-second",
        "prompt_sha256": "b" * 64,
        "source_publication_id": "publication-1",
        "source_publication_expires_at": NOW + timedelta(minutes=10),
        "publication_provider": "test",
        "provider_request_fingerprint": "d" * 64,
        "capability_snapshot_hash": "c" * 64,
        "metadata": {
            "simulated": False,
            "deterministic": False,
            "recovered": False,
        },
    }
    values.update(updates)
    return ProductionVideoClipEntry.model_validate(values)


def test_remote_record_canonical_round_trip_has_no_url_or_secret() -> None:
    content = serialize_remote_video_job(record())
    assert deserialize_remote_video_job(content) == record()
    assert content.endswith(b"\n")
    assert b"Authorization" not in content
    assert b"api_key" not in content
    assert b"https://" not in content
    assert b"?" not in content
    assert content == serialize_remote_video_job(record())


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xff",
        b"{",
        b'{"job_id":"a","job_id":"b"}',
        b'{"estimated_cost_usd":NaN}',
        b'{"estimated_cost_usd":Infinity}',
        b"[]",
    ],
)
def test_remote_record_deserialization_rejects_invalid_json(raw: bytes) -> None:
    with pytest.raises((UnicodeDecodeError, ValidationError, ValueError)):
        deserialize_remote_video_job(raw)


@pytest.mark.asyncio
async def test_in_memory_remote_store_create_checkpoint_and_recovery() -> None:
    store = InMemoryRemoteVideoJobStore()
    first = record()
    await store.create(first)
    updated = first.model_copy(
        update={
            "remote_status": OpenRouterRemoteStatus.IN_PROGRESS,
            "poll_attempts": 1,
            "last_polled_at": NOW,
        }
    )
    await store.checkpoint(previous=first, current=updated)
    assert (
        await store.read(
            job_id=JOB_ID,
            attempt_number=1,
            visual_asset_id=VISUAL_ASSET_ID,
        )
        == updated
    )
    assert store.checkpoints == 2


@pytest.mark.asyncio
async def test_remote_store_finds_older_active_job_past_terminal_retry() -> None:
    store = InMemoryRemoteVideoJobStore()
    active = record()
    terminal_retry = record(
        attempt_number=2,
        remote_job_id="job-2",
        remote_generation_id="generation-2",
        remote_status="failed",
        terminal_at=NOW,
        safe_remote_path="/api/v1/videos/job-2",
    )
    await store.create(active)
    await store.create(terminal_retry)

    assert (
        await store.find_latest(
            job_id=JOB_ID,
            before_attempt_number=3,
            visual_asset_id=VISUAL_ASSET_ID,
        )
        == active
    )


@pytest.mark.asyncio
async def test_remote_store_duplicate_create_and_stale_cas_conflict() -> None:
    store = InMemoryRemoteVideoJobStore()
    first = record()
    await store.create(first)
    with pytest.raises(RemoteVideoJobConflictError):
        await store.create(first)
    current = first.model_copy(update={"poll_attempts": 1, "last_polled_at": NOW})
    await store.checkpoint(previous=first, current=current)
    with pytest.raises(RemoteVideoJobConflictError):
        await store.checkpoint(previous=first, current=current)


@pytest.mark.parametrize(
    "field",
    [
        "job_id",
        "attempt_number",
        "visual_asset_id",
        "provider",
        "model",
        "source_image_sha256",
        "prompt_sha256",
        "capability_snapshot_hash",
        "provider_request_fingerprint",
        "publication_provider",
        "publication_id",
        "remote_job_id",
        "estimated_cost_usd",
        "pricing_snapshot_at",
        "pricing_sku",
        "safe_remote_path",
    ],
)
@pytest.mark.asyncio
async def test_remote_store_rejects_immutable_field_change(field: str) -> None:
    store = InMemoryRemoteVideoJobStore()
    first = record()
    await store.create(first)
    replacement: object = "changed"
    if field == "attempt_number":
        replacement = 2
    elif field in {
        "source_image_sha256",
        "prompt_sha256",
        "capability_snapshot_hash",
        "provider_request_fingerprint",
    }:
        replacement = "f" * 64
    elif field == "estimated_cost_usd":
        replacement = Decimal("0.05")
    elif field == "pricing_snapshot_at":
        replacement = NOW + timedelta(seconds=1)
    changed = first.model_copy(update={field: replacement})
    with pytest.raises(RemoteVideoJobConflictError):
        await store.checkpoint(previous=first, current=changed)


@pytest.mark.asyncio
async def test_remote_store_rejects_poll_attempt_decrease() -> None:
    store = InMemoryRemoteVideoJobStore()
    first = record(poll_attempts=2, last_polled_at=NOW)
    await store.create(first)
    with pytest.raises(RemoteVideoJobConflictError):
        await store.checkpoint(
            previous=first,
            current=first.model_copy(update={"poll_attempts": 1, "last_polled_at": NOW}),
        )


@pytest.mark.asyncio
async def test_local_remote_store_is_durable_atomic_and_contractual(
    tmp_path: Path,
) -> None:
    store = LocalRemoteVideoJobStore(tmp_path)
    first = record()
    await store.create(first)
    target = (
        tmp_path
        / "production"
        / str(JOB_ID)
        / "generating_video_clips"
        / "attempt-1"
        / "remote-jobs"
        / f"video-{VISUAL_ASSET_ID}.json"
    )
    assert target.is_file()
    assert not tuple(target.parent.glob("*.tmp"))
    assert (
        await store.read(
            job_id=JOB_ID,
            attempt_number=1,
            visual_asset_id=VISUAL_ASSET_ID,
        )
        == first
    )
    updated = first.model_copy(update={"poll_attempts": 1, "last_polled_at": NOW})
    await store.checkpoint(previous=first, current=updated)
    assert deserialize_remote_video_job(target.read_bytes()) == updated
    assert (
        await store.find_latest(
            job_id=JOB_ID,
            before_attempt_number=2,
            visual_asset_id=VISUAL_ASSET_ID,
        )
        == updated
    )


@pytest.mark.parametrize(
    "field",
    [
        "publication_expires_at",
        "submitted_at",
        "last_polled_at",
        "terminal_at",
        "pricing_snapshot_at",
    ],
)
def test_remote_record_rejects_naive_timestamps(field: str) -> None:
    naive = NOW.replace(tzinfo=None)
    updates: dict[str, object] = {field: naive}
    if field == "last_polled_at":
        updates["poll_attempts"] = 1
    if field == "terminal_at":
        updates["remote_status"] = OpenRouterRemoteStatus.FAILED
    with pytest.raises(ValidationError, match="timezone-aware"):
        record(**updates)


@pytest.mark.asyncio
async def test_remote_store_terminal_state_is_immutable() -> None:
    store = InMemoryRemoteVideoJobStore()
    terminal = record(
        remote_status=OpenRouterRemoteStatus.FAILED,
        terminal_at=NOW,
    )
    await store.create(terminal)

    changed = terminal.model_copy(update={"reported_cost_usd": Decimal("0.05")})
    with pytest.raises(RemoteVideoJobConflictError, match="terminal"):
        await store.checkpoint(previous=terminal, current=changed)


@pytest.mark.parametrize(
    "corruption",
    [
        b"",
        pytest.param(b"x" * 1_000_001, id="oversize"),
        b"{",
        b'{"job_id":"a","job_id":"b"}',
        b'{"estimated_cost_usd":NaN}',
    ],
)
@pytest.mark.asyncio
async def test_local_remote_store_rejects_corruption(tmp_path: Path, corruption: bytes) -> None:
    store = LocalRemoteVideoJobStore(tmp_path)
    first = record()
    await store.create(first)
    target = next(tmp_path.rglob("video-*.json"))
    target.write_bytes(corruption)
    with pytest.raises(RemoteVideoJobStoreError):
        await store.read(
            job_id=JOB_ID,
            attempt_number=1,
            visual_asset_id=VISUAL_ASSET_ID,
        )


@pytest.mark.asyncio
async def test_local_remote_store_rejects_traversal_identity(tmp_path: Path) -> None:
    store = LocalRemoteVideoJobStore(tmp_path)
    with pytest.raises(RemoteVideoJobStoreError):
        await store.read(
            job_id=JOB_ID,
            attempt_number=1,
            visual_asset_id="asset-s../../outside",
        )


@pytest.mark.parametrize(
    ("width", "height", "expected_ratio", "expected_dimensions"),
    [
        (1920, 1080, "16:9", (1280, 720)),
        (1080, 1920, "9:16", (720, 1280)),
        (1000, 1000, "1:1", (720, 720)),
        (4, 3, "1:1", (720, 720)),
        (3, 4, "9:16", (720, 1280)),
    ],
)
def test_openrouter_configuration_derives_closed_aspect_and_dimensions(
    width: int,
    height: int,
    expected_ratio: str,
    expected_dimensions: tuple[int, int],
) -> None:
    config = VideoClipGenerationConfiguration(provider="openrouter")
    assert config.aspect_ratio(width, height) == expected_ratio
    assert config.output_dimensions(width, height) == expected_dimensions


def test_simulated_configuration_fingerprint_remains_phase_5f1_compatible() -> None:
    config = VideoClipGenerationConfiguration()
    legacy = {
        "provider": "simulated",
        "model": "simulated-video-v1",
        "output_format": "mp4",
        "codec": "h264",
        "frame_rate": 24,
        "duration_seconds": 4.0,
        "max_duration_seconds": 10.0,
    }
    expected = hashlib.sha256(
        json.dumps(
            legacy,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    assert config.fingerprint() == expected


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [
        ("720p", (1280, 720)),
        ("1080p", (1920, 1080)),
    ],
)
def test_openrouter_resolution_controls_expected_dimensions(
    resolution: str, expected: tuple[int, int]
) -> None:
    config = VideoClipGenerationConfiguration(provider="openrouter", resolution=resolution)
    assert config.output_dimensions(16, 9) == expected


@pytest.mark.parametrize(
    "field",
    [
        "remote_job_id",
        "remote_status",
        "remote_submitted_at",
        "remote_poll_attempts",
        "estimated_cost_usd",
        "prompt_sha256",
        "source_publication_id",
        "publication_provider",
        "provider_request_fingerprint",
        "capability_snapshot_hash",
    ],
)
def test_stored_openrouter_entry_requires_complete_remote_metadata(
    field: str,
) -> None:
    with pytest.raises(ValidationError):
        openrouter_entry(**{field: None})


@pytest.mark.parametrize(
    "status",
    [
        VideoClipRemoteStatus.PENDING,
        VideoClipRemoteStatus.IN_PROGRESS,
        VideoClipRemoteStatus.FAILED,
        VideoClipRemoteStatus.CANCELLED,
        VideoClipRemoteStatus.EXPIRED,
    ],
)
def test_stored_openrouter_entry_requires_completed_remote_state(
    status: VideoClipRemoteStatus,
) -> None:
    with pytest.raises(ValidationError):
        openrouter_entry(remote_status=status)


def test_stored_openrouter_entry_accepts_safe_remote_metadata() -> None:
    entry = openrouter_entry()
    dumped = entry.model_dump(mode="json")
    assert dumped["remote_job_id"] == "job-1"
    assert dumped["remote_status"] == "completed"
    assert "url" not in dumped["remote_url_metadata"]
    assert entry.estimated_cost_usd == Decimal("0.04")


def test_reconciler_matches_complete_remote_provenance() -> None:
    remote = record(
        remote_status="completed",
        poll_attempts=2,
        last_polled_at=NOW,
        terminal_at=NOW,
        remote_content_available=True,
        reported_cost_usd="0.04",
    )
    assert _remote_matches_entry(remote, openrouter_entry())


@pytest.mark.parametrize(
    "marker",
    [
        b"https://signed.example/a",
        b"http://example/a",
        b"authorization",
        b"api_key",
        b"signed_url",
        b"content_url",
        b"polling_url",
        b"access_token",
        b"refresh_token",
        b"response_body",
        b"?signature=x",
        b"?token=x",
    ],
)
def test_reconciler_detects_sensitive_remote_metadata(marker: bytes) -> None:
    assert _contains_sensitive_remote_metadata(b'{"value":"' + marker + b'"}')


@pytest.mark.parametrize(
    "content",
    [
        serialize_remote_video_job(record()),
        b'{"safe_remote_path":"/api/v1/videos/job-1"}',
        b'{"publication_id":"opaque"}',
        b'{"prompt_sha256":"' + b"a" * 64 + b'"}',
    ],
)
def test_reconciler_allows_safe_remote_metadata(content: bytes) -> None:
    assert not _contains_sensitive_remote_metadata(content)


@pytest.mark.parametrize(
    "url",
    [
        "http://openrouter.ai/api/v1",
        "https://evil.example/api/v1",
        "https://localhost/api/v1",
        "https://user@openrouter.ai/api/v1",
        "https://openrouter.ai/api/v2",
        "https://openrouter.ai/api/v1?token=x",
        "https://openrouter.ai/api/v1#fragment",
        "file:///api/v1",
    ],
)
def test_openrouter_base_url_is_pinned(url: str) -> None:
    with pytest.raises(OpenRouterVideoConfigurationError):
        _validate_base_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://openrouter.ai/api/v1/videos/job",
        "https://evil.example/api/v1/videos/job",
        "https://user@openrouter.ai/api/v1/videos/job",
        "/api/v1/videos/other",
        "/api/v1/videos/job?token=x",
        "/api/v1/videos/job#fragment",
        "/api/v1/videos/job/../other",
    ],
)
def test_polling_url_is_pinned_and_secret_free(url: str) -> None:
    with pytest.raises(OpenRouterVideoInvalidResponseError):
        _validated_remote_path(url, "job")


@pytest.mark.parametrize(
    ("base_url", "follow_redirects"),
    [
        ("http://openrouter.ai", False),
        ("https://evil.example", False),
        ("https://localhost", False),
        ("https://user@openrouter.ai", False),
        ("https://openrouter.ai", True),
    ],
)
@pytest.mark.asyncio
async def test_http_client_policy_rejects_unsafe_client(
    base_url: str, follow_redirects: bool
) -> None:
    async with httpx.AsyncClient(
        base_url=base_url,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)),
        follow_redirects=follow_redirects,
    ) as client:
        with pytest.raises(OpenRouterVideoConfigurationError):
            _validate_client(client)


def test_settings_keep_simulated_default_and_secret_redacted(tmp_path: Path) -> None:
    settings = base_settings(tmp_path)
    assert settings.ORION_VIDEO_CLIP_GENERATION_PROVIDER == "simulated"
    assert settings.ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS is False
    assert settings.ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER == "disabled"


@pytest.mark.parametrize(
    ("billable", "key", "expected"),
    [
        (False, None, OpenRouterVideoConfigurationError),
        (True, None, OpenRouterVideoConfigurationError),
        (True, "fake-key", VideoFramePublicationUnavailableError),
    ],
)
def test_composition_openrouter_fails_before_network_without_real_publisher(
    tmp_path: Path,
    billable: bool,
    key: str | None,
    expected: type[Exception],
) -> None:
    values: dict[str, object] = {
        "ORION_VIDEO_CLIP_GENERATION_PROVIDER": "openrouter",
        "ORION_VIDEO_CLIP_GENERATION_MODEL": "test/video-model",
        "ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS": billable,
    }
    if key is not None:
        values["ORION_VIDEO_CLIP_GENERATION_OPENROUTER_API_KEY"] = SecretStr(key)
    configured = base_settings(tmp_path).model_copy(update=values)
    with pytest.raises(expected):
        build_production_container(configured)


def test_composition_builds_lazy_openrouter_video_with_shared_key_and_publisher(
    tmp_path: Path,
) -> None:
    configured = base_settings(tmp_path).model_copy(
        update={
            "ORION_OPENROUTER_API_KEY": SecretStr("fake-shared-key"),
            "ORION_VIDEO_CLIP_GENERATION_PROVIDER": "openrouter",
            "ORION_VIDEO_CLIP_GENERATION_MODEL": "google/veo-3.1-lite",
            "ORION_VIDEO_CLIP_GENERATION_ALLOW_BILLABLE_REQUESTS": True,
            "ORION_VIDEO_CLIP_GENERATION_MAX_ESTIMATED_COST_USD": Decimal("0.20"),
            "ORION_VIDEO_CLIP_GENERATION_MAX_REQUESTS_PER_JOB": 1,
            "ORION_VIDEO_CLIP_GENERATION_FRAME_PUBLISHER": "filesystem",
            "ORION_ASSET_PUBLISHING_PUBLISHER": "filesystem",
            "ORION_ASSET_PUBLISHING_PUBLIC_ROOT": tmp_path / "public",
            "ORION_ASSET_PUBLISHING_PUBLIC_BASE_URL": (
                "https://media.example.test/orion"
            ),
        }
    )
    container = build_production_container(configured)
    assert type(container.video_clip_generation_provider).__name__ == (
        "OpenRouterVideoClipGenerationProvider"
    )
    assert not (tmp_path / "public").exists()
    container.shutdown()


@pytest.mark.parametrize(
    "updates",
    [
        {"allow_billable_requests": False},
        {"max_estimated_cost_usd": Decimal("0")},
        {"max_poll_attempts": 0},
        {"max_poll_attempts": 1001},
        {"max_response_bytes": 0},
        {"max_video_bytes": 0},
        {"timeout_seconds": 0},
        {"poll_interval_seconds": 0},
        {"capability_cache_ttl_seconds": 0},
    ],
)
def test_openrouter_provider_configuration_is_strict(
    updates: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "model": "test/video-model",
        "resolution": "720p",
        "max_estimated_cost_usd": Decimal("1"),
        "allow_billable_requests": True,
    }
    values.update(updates)
    if updates == {"allow_billable_requests": False}:
        config = OpenRouterVideoProviderConfiguration.model_validate(values)
        assert config.allow_billable_requests is False
    else:
        with pytest.raises(ValidationError):
            OpenRouterVideoProviderConfiguration.model_validate(values)
