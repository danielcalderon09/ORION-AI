"""Strict contracts for the audited OpenRouter asynchronous video API."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel

OPENROUTER_VIDEO_CAPABILITY_SNAPSHOT_VERSION = "2026-07-24"


class OpenRouterRemoteStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class OpenRouterVideoUsage(ContractModel):
    cost: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    is_byok: bool | None = None


class OpenRouterVideoJob(ContractModel):
    id: str = Field(min_length=1, max_length=200)
    polling_url: str = Field(min_length=1, max_length=1000)
    status: OpenRouterRemoteStatus
    generation_id: str | None = Field(default=None, max_length=200)
    unsigned_urls: tuple[str, ...] = Field(default=(), max_length=10)
    usage: OpenRouterVideoUsage | None = None
    error: str | None = Field(default=None, max_length=1000)


class OpenRouterVideoModelCapability(ContractModel):
    id: str = Field(min_length=1, max_length=300)
    canonical_slug: str | None = Field(default=None, max_length=300)
    name: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    created: int | None = Field(default=None, ge=0)
    supported_durations: tuple[int, ...] = ()
    supported_resolutions: tuple[str, ...] = ()
    supported_aspect_ratios: tuple[str, ...] = ()
    supported_sizes: tuple[str, ...] | None = None
    supported_frame_images: tuple[str, ...] = ()
    generate_audio: bool | None = None
    seed: bool | None = None
    allowed_passthrough_parameters: tuple[str, ...] = ()
    pricing_skus: dict[str, Decimal] = Field(default_factory=dict)

    @field_validator("pricing_skus", mode="before")
    @classmethod
    def parse_prices(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("pricing_skus must be an object")
        return {key: Decimal(str(item)) for key, item in value.items()}

    @model_validator(mode="after")
    def validate_capability(self) -> OpenRouterVideoModelCapability:
        if len(set(self.supported_durations)) != len(self.supported_durations):
            raise ValueError("supported durations must be unique")
        return self

    def snapshot_hash(self) -> str:
        payload = {
            "version": OPENROUTER_VIDEO_CAPABILITY_SNAPSHOT_VERSION,
            "model": self.model_dump(mode="json"),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
        return hashlib.sha256(raw).hexdigest()


class OpenRouterVideoModelsResponse(ContractModel):
    data: tuple[OpenRouterVideoModelCapability, ...] = Field(max_length=500)


class PublishedVideoFrameImage(ContractModel):
    url: str = Field(repr=False, exclude=True, min_length=1, max_length=4096)
    expires_at: datetime | None = None
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str
    size_bytes: int = Field(gt=0, le=250_000_000)
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    publication_provider: str = Field(min_length=1, max_length=100)
    publication_id: str = Field(min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {
            "signed_url",
            "url",
            "polling_url",
            "content_url",
            "response_body",
            "authorization",
            "api_key",
            "token",
        }
        if any(str(key).lower() in forbidden for key in value):
            raise ValueError("publication metadata contains a sensitive field")
        result = validate_safe_json(value, path="published_frame.metadata")
        if not isinstance(result, dict):
            raise ValueError("publication metadata must be an object")
        return result


class VideoMotionPrompt(ContractModel):
    text: str = Field(repr=False, exclude=True, min_length=1, max_length=2000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    version: str = "openrouter-motion-v1"


class OpenRouterVideoProviderConfiguration(ContractModel):
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = Field(min_length=1, max_length=300)
    resolution: str = Field(min_length=1, max_length=30)
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    poll_interval_seconds: float = Field(default=5, gt=0, le=300)
    max_poll_seconds: float = Field(default=900, gt=0, le=7200)
    max_poll_attempts: int = Field(default=180, ge=1, le=1000)
    max_response_bytes: int = Field(default=2_000_000, ge=1, le=20_000_000)
    max_video_bytes: int = Field(default=50_000_000, ge=1, le=250_000_000)
    capability_cache_ttl_seconds: float = Field(default=3600, gt=0, le=86_400)
    max_estimated_cost_usd: Decimal = Field(gt=0, max_digits=18, decimal_places=9)
    allow_billable_requests: bool = False

    @field_validator("max_estimated_cost_usd", mode="before")
    @classmethod
    def reject_float_cost(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("OpenRouter video cost must not use float")
        return value


class RemoteVideoJobRecord(ContractModel):
    schema_version: str = "1.0.0"
    job_id: str
    attempt_number: int = Field(ge=1)
    visual_asset_id: str = Field(
        pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$"
    )
    provider: str = "openrouter"
    model: str
    source_image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    capability_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    publication_provider: str
    publication_id: str
    publication_expires_at: datetime | None = None
    remote_job_id: str
    remote_generation_id: str | None = None
    remote_status: OpenRouterRemoteStatus
    submitted_at: datetime
    last_polled_at: datetime | None = None
    poll_attempts: int = Field(default=0, ge=0)
    terminal_at: datetime | None = None
    remote_content_available: bool = False
    estimated_cost_usd: Decimal = Field(ge=0, max_digits=18, decimal_places=9)
    reported_cost_usd: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=9)
    pricing_snapshot_at: datetime
    pricing_sku: str
    safe_remote_path: str

    @model_validator(mode="after")
    def validate_remote_job(self) -> RemoteVideoJobRecord:
        try:
            UUID(self.job_id)
        except ValueError as exc:
            raise ValueError("remote video local job ID is invalid") from exc
        if self.provider != "openrouter":
            raise ValueError("remote video provider must be OpenRouter")
        if (
            not self.remote_job_id
            or any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for character in self.remote_job_id
            )
            or self.safe_remote_path
            != f"/api/v1/videos/{self.remote_job_id}"
        ):
            raise ValueError("remote video path is not contractual")
        terminal = self.remote_status in {
            OpenRouterRemoteStatus.COMPLETED,
            OpenRouterRemoteStatus.FAILED,
            OpenRouterRemoteStatus.CANCELLED,
            OpenRouterRemoteStatus.EXPIRED,
        }
        if terminal != (self.terminal_at is not None):
            raise ValueError("remote video terminal timestamp is inconsistent")
        if (
            self.remote_content_available
            != (self.remote_status is OpenRouterRemoteStatus.COMPLETED)
        ):
            raise ValueError("remote video content availability is inconsistent")
        return self
