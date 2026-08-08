"""Neutral publication port for OpenRouter first-frame inputs."""

from __future__ import annotations

import hashlib
import ipaddress
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import unquote, urlsplit

from backend.src.production.asset_publishing.models import PublishableAsset
from backend.src.production.asset_publishing.ports import AssetPublisher
from backend.src.production.video_clip_generation.exceptions import (
    VideoFramePublicationUnavailableError,
)
from backend.src.production.video_clip_generation.ports import (
    VideoClipProviderRequest,
)
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    PublishedVideoFrameImage,
)


class VideoFrameImagePublisher(Protocol):
    is_real: bool

    async def publish_first_frame(
        self, source: VideoClipProviderRequest
    ) -> PublishedVideoFrameImage: ...

    async def close(self) -> None: ...


class DisabledVideoFrameImagePublisher:
    is_real = False

    async def publish_first_frame(
        self, source: VideoClipProviderRequest
    ) -> PublishedVideoFrameImage:
        del source
        raise VideoFramePublicationUnavailableError(
            "a secure public frame publisher is not configured"
        )

    async def close(self) -> None:
        return None


class InMemoryVideoFrameImagePublisher:
    """Test-only publisher returning controlled HTTPS references without I/O."""

    is_real = False

    def __init__(
        self,
        *,
        host: str = "frames.example.test",
        lifetime_seconds: int = 600,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._host = host
        self._lifetime = lifetime_seconds
        self._clock = clock
        self.calls = 0
        self.closed = False

    async def publish_first_frame(
        self, source: VideoClipProviderRequest
    ) -> PublishedVideoFrameImage:
        if self.closed:
            raise VideoFramePublicationUnavailableError("frame publisher is closed")
        publication_id = hashlib.sha256(
            f"{source.visual_asset_id}:{source.source_image_sha256}".encode()
        ).hexdigest()[:32]
        url = f"https://{self._host}/frames/{publication_id}"
        validate_public_frame_url(url)
        self.calls += 1
        return PublishedVideoFrameImage(
            url=url,
            expires_at=self._clock() + timedelta(seconds=self._lifetime),
            content_sha256=source.source_image_sha256,
            content_type=source.source_image_mime_type,
            size_bytes=source.source_image_size_bytes,
            width=source.source_image_width,
            height=source.source_image_height,
            publication_provider="in_memory_test",
            publication_id=publication_id,
            metadata={"host": self._host},
        )

    async def close(self) -> None:
        self.closed = True


class PublishedAssetVideoFrameImagePublisher:
    """Publish one verified SOURCE_IMAGE through the existing HTTPS publisher."""

    is_real = True

    def __init__(
        self,
        *,
        publisher: AssetPublisher,
        lifetime_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if publisher.name in {"null", "future_cloud_unavailable"}:
            raise VideoFramePublicationUnavailableError(
                "a real asset publisher is required for video first frames"
            )
        if not 30 <= lifetime_seconds <= 86_400:
            raise ValueError("video frame publication lifetime is outside safe bounds")
        self._publisher = publisher
        self._lifetime = lifetime_seconds
        self._clock = clock

    async def publish_first_frame(
        self, source: VideoClipProviderRequest
    ) -> PublishedVideoFrameImage:
        now = self._aware_now()
        extension = _image_extension(source.source_image_mime_type)
        receipt = await self._publisher.publish(
            asset=PublishableAsset(
                asset_id=f"frame-{source.visual_asset_id}",
                binary_asset_id=f"frame-{source.visual_asset_id}",
                source_hash=source.source_image_sha256,
                content_type=source.source_image_mime_type,
                extension=extension,
                size_bytes=source.source_image_size_bytes,
                content=source.source_image_content,
                source_manifest_kind="image_acquisition",
                source_manifest_sha256=source.source_image_sha256,
                source_artifact_id=source.source_image_artifact_id,
                metadata={"visual_asset_id": source.visual_asset_id},
            ),
            expires_at=now + timedelta(seconds=self._lifetime),
        )
        validate_public_frame_url(receipt.public_url)
        return PublishedVideoFrameImage(
            url=receipt.public_url,
            expires_at=receipt.expires_at,
            content_sha256=receipt.source_hash,
            content_type=receipt.content_type,
            size_bytes=receipt.size_bytes,
            width=source.source_image_width,
            height=source.source_image_height,
            publication_provider=receipt.publisher,
            publication_id=receipt.publication_id,
            metadata={"host": urlsplit(receipt.public_url).hostname or "unknown"},
        )

    async def close(self) -> None:
        # The shared publisher is owned and closed by the production container.
        return None

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise VideoFramePublicationUnavailableError(
                "video frame publication clock must be timezone-aware"
            )
        return value


def validate_public_frame_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise VideoFramePublicationUnavailableError("published frame URL is not a safe HTTPS URL")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise VideoFramePublicationUnavailableError("published frame URL host is not public")
    if ".." in PurePosixPath(unquote(parsed.path)).parts:
        raise VideoFramePublicationUnavailableError("published frame URL path is unsafe")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise VideoFramePublicationUnavailableError("published frame URL address is not public")
    return value


def _image_extension(content_type: str) -> str:
    try:
        return {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }[content_type]
    except KeyError as exc:
        raise VideoFramePublicationUnavailableError(
            "video first-frame image type is unsupported"
        ) from exc
