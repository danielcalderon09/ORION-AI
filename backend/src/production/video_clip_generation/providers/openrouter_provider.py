"""Secure asynchronous OpenRouter image-to-video provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoConfigurationError,
    OpenRouterVideoContentTypeError,
    OpenRouterVideoCostPolicyError,
    OpenRouterVideoDownloadError,
    OpenRouterVideoError,
    OpenRouterVideoInvalidResponseError,
    OpenRouterVideoRemoteCancelledError,
    OpenRouterVideoRemoteExpiredError,
    OpenRouterVideoRemoteFailedError,
    OpenRouterVideoResponseTooLargeError,
    OpenRouterVideoTimeoutError,
    OpenRouterVideoUncertainSubmissionError,
    VideoFramePublicationUnavailableError,
)
from backend.src.production.video_clip_generation.frame_image_publisher import (
    VideoFrameImagePublisher,
    validate_public_frame_url,
)
from backend.src.production.video_clip_generation.ports import (
    GeneratedVideoClipPayload,
    VideoClipProviderRequest,
    VideoClipProviderResponse,
)
from backend.src.production.video_clip_generation.prompt_builder import (
    VideoMotionPromptBuilder,
)
from backend.src.production.video_clip_generation.providers.openrouter_capabilities import (
    OpenRouterVideoModelCapabilityResolver,
    _read_bounded,
    _strict_json,
)
from backend.src.production.video_clip_generation.providers.openrouter_error_classifier import (
    raise_for_openrouter_status,
)
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    OpenRouterRemoteStatus,
    OpenRouterVideoJob,
    OpenRouterVideoProviderConfiguration,
    OpenRouterVideoRequestStatus,
    PublishedVideoFrameImage,
    RemoteVideoJobRecord,
)
from backend.src.production.video_clip_generation.remote_job_store import (
    LocalRemoteVideoJobStore,
)
from backend.src.production.video_clip_generation.remote_jobs import (
    BillableVideoGenerationPolicy,
    OpenRouterVideoPollingPolicy,
)

_REMOTE_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


class RemoteVideoJobStore(Protocol):
    async def read(
        self, *, job_id: UUID, attempt_number: int, visual_asset_id: str
    ) -> RemoteVideoJobRecord | None: ...

    async def create(self, record: RemoteVideoJobRecord) -> None: ...

    async def count_for_job(self, *, job_id: UUID) -> int: ...

    async def find_latest(
        self,
        *,
        job_id: UUID,
        before_attempt_number: int,
        visual_asset_id: str,
    ) -> RemoteVideoJobRecord | None: ...

    async def checkpoint(
        self, *, previous: RemoteVideoJobRecord, current: RemoteVideoJobRecord
    ) -> None: ...


class OpenRouterVideoClipGenerationProvider:
    """Submit once, checkpoint the remote ID, then poll and download."""

    def __init__(
        self,
        *,
        api_key: str,
        configuration: OpenRouterVideoProviderConfiguration,
        client: httpx.AsyncClient,
        capability_resolver: OpenRouterVideoModelCapabilityResolver,
        frame_publisher: VideoFrameImagePublisher,
        remote_job_store: RemoteVideoJobStore,
        cost_policy: BillableVideoGenerationPolicy,
        polling_policy: OpenRouterVideoPollingPolicy,
        prompt_builder: VideoMotionPromptBuilder,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
        owns_client: bool = False,
    ) -> None:
        if not api_key.strip():
            raise OpenRouterVideoConfigurationError("OpenRouter video credential is missing")
        _validate_base_url(configuration.base_url)
        _validate_client(client)
        if not configuration.allow_billable_requests:
            raise OpenRouterVideoConfigurationError(
                "OpenRouter video billable requests are disabled"
            )
        self._authorization = f"Bearer {api_key}"
        self._config = configuration
        self._client = client
        self._client.headers["Authorization"] = self._authorization
        self._client.headers["User-Agent"] = "ORION-AI/video-clip-generation"
        self._resolver = capability_resolver
        self._publisher = frame_publisher
        self._jobs = remote_job_store
        self._cost_policy = cost_policy
        self._polling = polling_policy
        self._prompt_builder = prompt_builder
        self._clock = clock
        self._monotonic = monotonic_clock
        self._owns_client = owns_client
        self._closed = False

    async def generate_clip(self, request: VideoClipProviderRequest) -> VideoClipProviderResponse:
        if self._closed:
            raise OpenRouterVideoConfigurationError("OpenRouter video provider is closed")
        if request.configuration.provider != "openrouter":
            raise OpenRouterVideoConfigurationError(
                "OpenRouter provider received a non-OpenRouter request"
            )
        if request.configuration.generate_audio:
            raise OpenRouterVideoConfigurationError("video audio must remain disabled")
        try:
            duration = _integer_duration(request.duration_seconds)
            aspect_ratio = request.configuration.aspect_ratio(
                request.source_image_width, request.source_image_height
            )
            prompt = self._prompt_builder.build(request)
        except OpenRouterVideoError as exc:
            exc.add_diagnostic(
                phase="request_construction",
                code="request_invalid",
                metadata={"source_image_sha256": request.source_image_sha256},
            )
            raise
        except (TypeError, ValueError) as exc:
            raise OpenRouterVideoConfigurationError(
                "OpenRouter video request construction failed",
                diagnostic_phase="request_construction",
                diagnostic_code="request_invalid",
                diagnostic_metadata={"source_image_sha256": request.source_image_sha256},
            ) from exc
        started = self._monotonic()
        existing = await self._jobs.read(
            job_id=request.job_id,
            attempt_number=request.attempt_number,
            visual_asset_id=request.visual_asset_id,
        )
        if existing is None and request.attempt_number > 1:
            previous = await self._jobs.find_latest(
                job_id=request.job_id,
                before_attempt_number=request.attempt_number,
                visual_asset_id=request.visual_asset_id,
            )
            if previous is not None:
                existing = previous
        if existing is not None:
            self._validate_recovery(existing, request, prompt.sha256)
            if existing.request_status is OpenRouterVideoRequestStatus.PREPARED:
                publication = await self._publisher.publish_first_frame(request)
                validate_public_frame_url(publication.url)
                self._validate_publication(publication, request)
                if publication.publication_id != existing.publication_id:
                    raise OpenRouterVideoConfigurationError(
                        "prepared video publication identity changed"
                    )
                terminal = await self._submit_prepared(
                    existing,
                    prompt=prompt.text,
                    first_frame_url=publication.url,
                )
            elif existing.request_status in {
                OpenRouterVideoRequestStatus.SUBMITTING,
                OpenRouterVideoRequestStatus.UNCERTAIN,
            }:
                raise OpenRouterVideoUncertainSubmissionError(
                    "video submission outcome requires manual review"
                )
            elif existing.request_status is OpenRouterVideoRequestStatus.FAILED:
                if existing.remote_status is not None:
                    terminal = self._terminal_or_raise(existing)
                raise OpenRouterVideoRemoteFailedError("video submission failed permanently")
            else:
                terminal = await self._poll(existing)
        else:
            if (
                await self._jobs.count_for_job(job_id=request.job_id)
                >= self._config.max_requests_per_job
            ):
                raise OpenRouterVideoCostPolicyError(
                    "OpenRouter video paid submission limit was reached",
                    diagnostic_phase="cost_authorization",
                    diagnostic_code="request_limit_exceeded",
                    diagnostic_metadata={
                        "max_estimated_cost_usd": str(
                            self._config.max_estimated_cost_usd
                        ),
                    },
                )
            try:
                publication = await self._publisher.publish_first_frame(request)
                validate_public_frame_url(publication.url)
                self._validate_publication(publication, request)
            except OpenRouterVideoError as exc:
                self._add_pre_submission_diagnostic(
                    exc,
                    request=request,
                    duration=duration,
                    aspect_ratio=aspect_ratio,
                    phase="publication",
                    code="publication_invalid",
                )
                raise
            try:
                capability = await self._resolver.resolve(
                    model=self._config.model,
                    duration=duration,
                    resolution=self._config.resolution,
                    aspect_ratio=aspect_ratio,
                )
            except OpenRouterVideoError as exc:
                self._add_pre_submission_diagnostic(
                    exc,
                    request=request,
                    duration=duration,
                    aspect_ratio=aspect_ratio,
                    publication=publication,
                    phase="capability_discovery",
                    code="capability_error",
                )
                raise
            try:
                estimated, pricing_sku = self._cost_policy.authorize(
                    provider="openrouter",
                    capability=capability,
                    duration_seconds=duration,
                    resolution=self._config.resolution,
                    output_count=1,
                    has_remote_job=False,
                    has_recoverable_clip=False,
                )
            except OpenRouterVideoError as exc:
                exc.add_diagnostic(
                    metadata={
                        "capability_endpoint_status": 200,
                        "capability_model_found": True,
                    }
                )
                self._add_pre_submission_diagnostic(
                    exc,
                    request=request,
                    duration=duration,
                    aspect_ratio=aspect_ratio,
                    publication=publication,
                    phase="cost_authorization",
                    code="cost_policy_error",
                )
                raise
            fingerprint = _request_fingerprint(
                request=request,
                prompt_sha256=prompt.sha256,
                capability_hash=capability.snapshot_hash(),
                publication_id=publication.publication_id,
                aspect_ratio=aspect_ratio,
            )
            now = self._aware_now()
            prepared = RemoteVideoJobRecord(
                job_id=str(request.job_id),
                attempt_number=request.attempt_number,
                visual_asset_id=request.visual_asset_id,
                model=self._config.model,
                source_image_sha256=request.source_image_sha256,
                prompt_sha256=prompt.sha256,
                capability_snapshot_hash=capability.snapshot_hash(),
                provider_request_fingerprint=fingerprint,
                publication_provider=publication.publication_provider,
                publication_id=publication.publication_id,
                publication_expires_at=publication.expires_at,
                request_status=OpenRouterVideoRequestStatus.PREPARED,
                fresh_submission_permitted=True,
                prepared_at=now,
                requested_duration_seconds=duration,
                requested_resolution=self._config.resolution,
                requested_aspect_ratio=aspect_ratio,
                generate_audio=False,
                estimated_cost_usd=estimated,
                pricing_snapshot_at=now,
                pricing_sku=pricing_sku,
            )
            await self._jobs.create(prepared)
            terminal = await self._submit_prepared(
                prepared,
                prompt=prompt.text,
                first_frame_url=publication.url,
            )
        if terminal.remote_job_id is None:
            raise OpenRouterVideoUncertainSubmissionError(
                "remote video identity is unavailable"
            )
        content, content_sha256 = await self._download(terminal.remote_job_id)
        latency = max(0.0, (self._monotonic() - started) * 1000)
        metadata = _safe_response_metadata(terminal, content_sha256)
        return VideoClipProviderResponse(
            clips=(
                GeneratedVideoClipPayload(
                    content=content,
                    mime_type="video/mp4",
                    index=0,
                    provider_metadata={"sha256": content_sha256},
                ),
            ),
            provider="openrouter",
            requested_model=self._config.model,
            reported_model=terminal.reported_model or self._config.model,
            request_id=terminal.remote_job_id,
            latency_ms=latency,
            cost_usd=terminal.reported_cost_usd,
            finish_reason="completed",
            metadata=metadata,
        )

    def _add_pre_submission_diagnostic(
        self,
        error: OpenRouterVideoError,
        *,
        request: VideoClipProviderRequest,
        duration: int,
        aspect_ratio: str,
        phase: str,
        code: str,
        publication: PublishedVideoFrameImage | None = None,
    ) -> None:
        metadata: dict[str, object] = {
            "requested_model": self._config.model,
            "requested_duration_seconds": duration,
            "requested_resolution": self._config.resolution,
            "requested_aspect_ratio": aspect_ratio,
            "generate_audio": False,
            "max_estimated_cost_usd": str(self._config.max_estimated_cost_usd),
            "source_image_sha256": request.source_image_sha256,
        }
        if publication is not None:
            metadata["publication_id"] = publication.publication_id
        error.add_diagnostic(phase=phase, code=code, metadata=metadata)

    async def _submit_prepared(
        self,
        prepared: RemoteVideoJobRecord,
        *,
        prompt: str,
        first_frame_url: str,
    ) -> RemoteVideoJobRecord:
        if not prepared.fresh_submission_permitted:
            raise OpenRouterVideoUncertainSubmissionError(
                "fresh video submission is not permitted"
            )
        submitting = prepared.model_copy(
            update={
                "request_status": OpenRouterVideoRequestStatus.SUBMITTING,
                "fresh_submission_permitted": False,
                "submission_started_at": self._aware_now(),
            }
        )
        await self._jobs.checkpoint(previous=prepared, current=submitting)
        try:
            submitted = await self._submit(
                model=self._config.model,
                prompt=prompt,
                duration=prepared.requested_duration_seconds or 0,
                resolution=prepared.requested_resolution or "",
                aspect_ratio=prepared.requested_aspect_ratio or "",
                first_frame_url=first_frame_url,
            )
        except asyncio.CancelledError:
            await self._checkpoint_unresolved(submitting)
            raise
        except OpenRouterVideoUncertainSubmissionError:
            await self._checkpoint_unresolved(submitting)
            raise
        except OpenRouterVideoError as exc:
            failed = submitting.model_copy(
                update={
                    "request_status": OpenRouterVideoRequestStatus.FAILED,
                    "submission_http_status": getattr(exc, "http_status", None),
                }
            )
            await self._jobs.checkpoint(previous=submitting, current=failed)
            raise
        now = self._aware_now()
        accepted = submitting.model_copy(
            update={
                "request_status": (
                    OpenRouterVideoRequestStatus.COMPLETED
                    if submitted.status is OpenRouterRemoteStatus.COMPLETED
                    else OpenRouterVideoRequestStatus.SUBMITTED
                ),
                "submission_http_status": 202,
                "reported_model": submitted.model,
                "remote_job_id": submitted.id,
                "remote_generation_id": submitted.generation_id,
                "remote_status": submitted.status,
                "submitted_at": now,
                "terminal_at": now if self._polling.terminal(submitted.status) else None,
                "remote_content_available": (
                    submitted.status is OpenRouterRemoteStatus.COMPLETED
                ),
                "reported_cost_usd": (
                    submitted.usage.cost if submitted.usage is not None else None
                ),
                "safe_remote_path": f"/api/v1/videos/{submitted.id}",
            }
        )
        try:
            await self._jobs.checkpoint(previous=submitting, current=accepted)
        except Exception as exc:
            raise OpenRouterVideoUncertainSubmissionError(
                "remote video was accepted but its checkpoint failed"
            ) from exc
        return await self._poll(accepted)

    async def _checkpoint_unresolved(self, submitting: RemoteVideoJobRecord) -> None:
        uncertain = submitting.model_copy(
            update={"request_status": OpenRouterVideoRequestStatus.UNCERTAIN}
        )
        await self._jobs.checkpoint(previous=submitting, current=uncertain)

    async def _submit(
        self,
        *,
        model: str,
        prompt: str,
        duration: int,
        resolution: str,
        aspect_ratio: str,
        first_frame_url: str,
    ) -> OpenRouterVideoJob:
        payload = {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "generate_audio": False,
            "frame_images": [
                {
                    "type": "image_url",
                    "image_url": {"url": first_frame_url},
                    "frame_type": "first_frame",
                }
            ],
        }
        try:
            response = await self._client.post(
                "/api/v1/videos",
                headers=self._json_headers(),
                json=payload,
            )
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
            raise OpenRouterVideoUncertainSubmissionError(
                "OpenRouter video submission outcome is uncertain"
            ) from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            raise OpenRouterVideoUncertainSubmissionError(
                "OpenRouter video submission outcome is uncertain"
            ) from exc
        except httpx.RequestError as exc:
            raise OpenRouterVideoUncertainSubmissionError(
                "OpenRouter video submission outcome is uncertain"
            ) from exc
        if response.status_code != 202:
            raise_for_openrouter_status(response.status_code, operation="submit")
            raise OpenRouterVideoInvalidResponseError(
                "OpenRouter video submit did not return HTTP 202"
            )
        try:
            result = OpenRouterVideoJob.model_validate(
                _strict_json(await _read_bounded(response, self._config.max_response_bytes))
            )
            _validate_job_identity(result)
            _validated_remote_path(result.polling_url, result.id)
            if result.status not in {
                OpenRouterRemoteStatus.PENDING,
                OpenRouterRemoteStatus.IN_PROGRESS,
                OpenRouterRemoteStatus.COMPLETED,
            }:
                raise ValueError("submit returned an invalid initial state")
            return result
        except (
            UnicodeDecodeError,
            ValueError,
            OpenRouterVideoInvalidResponseError,
            OpenRouterVideoResponseTooLargeError,
        ) as exc:
            raise OpenRouterVideoUncertainSubmissionError(
                "OpenRouter accepted the request but returned an invalid job"
            ) from exc

    async def _poll(self, record: RemoteVideoJobRecord) -> RemoteVideoJobRecord:
        if record.remote_status is not None and self._polling.terminal(record.remote_status):
            return self._terminal_or_raise(record)
        if record.safe_remote_path is None:
            raise OpenRouterVideoUncertainSubmissionError(
                "remote video polling path is unavailable"
            )
        polling_path = record.safe_remote_path
        started = self._polling.monotonic()
        current = record
        if current.request_status is OpenRouterVideoRequestStatus.SUBMITTED:
            polling = current.model_copy(
                update={"request_status": OpenRouterVideoRequestStatus.POLLING}
            )
            await self._jobs.checkpoint(previous=current, current=polling)
            current = polling
        while True:
            if (
                current.poll_attempts >= self._polling.max_attempts
                or self._polling.monotonic() - started >= self._polling.max_seconds
            ):
                raise OpenRouterVideoUncertainSubmissionError(
                    "remote video remains active after the bounded polling window"
                )
            delay = self._polling.delay(current.poll_attempts + 1, None)
            await self._polling.sleeper(delay)
            try:
                response = await self._client.get(
                    polling_path,
                    headers=self._json_headers(),
                )
            except httpx.TimeoutException as exc:
                raise OpenRouterVideoUncertainSubmissionError(
                    "remote video polling timed out"
                ) from exc
            except httpx.RequestError as exc:
                raise OpenRouterVideoUncertainSubmissionError(
                    "remote video polling transport failed"
                ) from exc
            if response.status_code in {404, 429} or response.status_code >= 500:
                attempts = current.poll_attempts + 1
                retry_after = _retry_after(response)
                checkpoint = current.model_copy(
                    update={
                        "last_polled_at": self._aware_now(),
                        "poll_attempts": attempts,
                    }
                )
                await self._jobs.checkpoint(previous=current, current=checkpoint)
                current = checkpoint
                if retry_after is not None:
                    await self._polling.sleeper(self._polling.delay(attempts, retry_after))
                continue
            raise_for_openrouter_status(response.status_code, operation="poll")
            try:
                job = OpenRouterVideoJob.model_validate(
                    _strict_json(await _read_bounded(response, self._config.max_response_bytes))
                )
                _validate_job_identity(job, expected=current.remote_job_id)
                _validated_remote_path(job.polling_url, job.id)
            except (UnicodeDecodeError, ValueError) as exc:
                raise OpenRouterVideoUncertainSubmissionError(
                    "remote video polling response is invalid"
                ) from exc
            now = self._aware_now()
            updated = current.model_copy(
                update={
                    "request_status": (
                        OpenRouterVideoRequestStatus.COMPLETED
                        if job.status is OpenRouterRemoteStatus.COMPLETED
                        else OpenRouterVideoRequestStatus.FAILED
                        if self._polling.terminal(job.status)
                        else OpenRouterVideoRequestStatus.POLLING
                    ),
                    "reported_model": job.model or current.reported_model,
                    "remote_generation_id": (job.generation_id or current.remote_generation_id),
                    "remote_status": job.status,
                    "last_polled_at": now,
                    "poll_attempts": current.poll_attempts + 1,
                    "terminal_at": now if self._polling.terminal(job.status) else None,
                    "remote_content_available": (job.status is OpenRouterRemoteStatus.COMPLETED),
                    "reported_cost_usd": (job.usage.cost if job.usage is not None else None),
                }
            )
            await self._jobs.checkpoint(previous=current, current=updated)
            current = updated
            if self._polling.terminal(job.status):
                return self._terminal_or_raise(current)

    async def _download(self, remote_job_id: str) -> tuple[bytes, str]:
        _validate_remote_id(remote_job_id)
        try:
            async with self._client.stream(
                "GET",
                f"/api/v1/videos/{remote_job_id}/content",
                params={"index": 0},
                headers={
                    "Authorization": self._authorization,
                    "Accept": "video/mp4",
                },
            ) as response:
                raise_for_openrouter_status(response.status_code, operation="download")
                content_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if content_type != "video/mp4":
                    raise OpenRouterVideoContentTypeError("OpenRouter video download is not MP4")
                content = await _read_bounded(response, self._config.max_video_bytes)
        except asyncio.CancelledError:
            raise
        except OpenRouterVideoContentTypeError:
            raise
        except OpenRouterVideoResponseTooLargeError:
            raise
        except httpx.TimeoutException as exc:
            raise OpenRouterVideoTimeoutError("OpenRouter video download timed out") from exc
        except httpx.RequestError as exc:
            raise OpenRouterVideoDownloadError(
                "OpenRouter video download transport failed"
            ) from exc
        if not content or b"ftyp" not in content[:64]:
            raise OpenRouterVideoDownloadError(
                "OpenRouter video download is empty or not an MP4 container"
            )
        return content, hashlib.sha256(content).hexdigest()

    def _terminal_or_raise(self, record: RemoteVideoJobRecord) -> RemoteVideoJobRecord:
        if record.remote_status is OpenRouterRemoteStatus.COMPLETED:
            return record
        if record.remote_status is OpenRouterRemoteStatus.FAILED:
            raise OpenRouterVideoRemoteFailedError("remote video generation failed")
        if record.remote_status is OpenRouterRemoteStatus.CANCELLED:
            raise OpenRouterVideoRemoteCancelledError("remote video generation was cancelled")
        if record.remote_status is OpenRouterRemoteStatus.EXPIRED:
            raise OpenRouterVideoRemoteExpiredError("remote video generation expired")
        raise OpenRouterVideoInvalidResponseError("remote video state is not terminal")

    def _validate_recovery(
        self,
        record: RemoteVideoJobRecord,
        request: VideoClipProviderRequest,
        prompt_sha256: str,
    ) -> None:
        aspect_ratio = request.configuration.aspect_ratio(
            request.source_image_width,
            request.source_image_height,
        )
        expected_fingerprint = _request_fingerprint(
            request=request,
            prompt_sha256=prompt_sha256,
            capability_hash=record.capability_snapshot_hash,
            publication_id=record.publication_id,
            aspect_ratio=aspect_ratio,
        )
        if (
            record.model != self._config.model
            or record.source_image_sha256 != request.source_image_sha256
            or record.prompt_sha256 != prompt_sha256
            or record.provider_request_fingerprint != expected_fingerprint
        ):
            raise OpenRouterVideoConfigurationError("remote video recovery provenance differs")

    def _validate_publication(
        self,
        publication: PublishedVideoFrameImage,
        request: VideoClipProviderRequest,
    ) -> None:
        if (
            publication.content_sha256 != request.source_image_sha256
            or publication.content_type != request.source_image_mime_type
            or publication.size_bytes != request.source_image_size_bytes
            or publication.width != request.source_image_width
            or publication.height != request.source_image_height
        ):
            raise VideoFramePublicationUnavailableError(
                "published first frame differs from verified source"
            )
        if publication.expires_at is not None and publication.expires_at <= self._aware_now():
            raise VideoFramePublicationUnavailableError("published first frame has expired")

    def _json_headers(self) -> dict[str, str]:
        return {
            "Authorization": self._authorization,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise OpenRouterVideoConfigurationError("video provider clock must be aware")
        return value

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()


def create_openrouter_video_provider(
    *,
    api_key: str,
    configuration: OpenRouterVideoProviderConfiguration,
    frame_publisher: VideoFrameImagePublisher,
    workspace_root: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> OpenRouterVideoClipGenerationProvider:
    """Build the production adapter without performing startup network I/O."""

    if not frame_publisher.is_real:
        raise VideoFramePublicationUnavailableError(
            "OpenRouter video requires a real secure frame publisher"
        )
    timeout = httpx.Timeout(
        connect=configuration.timeout_seconds,
        read=configuration.timeout_seconds,
        write=configuration.timeout_seconds,
        pool=configuration.timeout_seconds,
    )
    client = httpx.AsyncClient(
        base_url="https://openrouter.ai",
        timeout=timeout,
        limits=httpx.Limits(
            max_connections=10,
            max_keepalive_connections=5,
        ),
        follow_redirects=False,
        trust_env=False,
    )
    return OpenRouterVideoClipGenerationProvider(
        api_key=api_key,
        configuration=configuration,
        client=client,
        capability_resolver=OpenRouterVideoModelCapabilityResolver(
            client=client,
            max_response_bytes=configuration.max_response_bytes,
            cache_ttl_seconds=configuration.capability_cache_ttl_seconds,
            monotonic=monotonic,
        ),
        frame_publisher=frame_publisher,
        remote_job_store=LocalRemoteVideoJobStore(workspace_root),
        cost_policy=BillableVideoGenerationPolicy(
            allow_billable_requests=configuration.allow_billable_requests,
            max_estimated_cost_usd=configuration.max_estimated_cost_usd,
        ),
        polling_policy=OpenRouterVideoPollingPolicy(
            interval_seconds=configuration.poll_interval_seconds,
            max_seconds=configuration.max_poll_seconds,
            max_attempts=configuration.max_poll_attempts,
            monotonic=monotonic,
            sleeper=asyncio.sleep,
        ),
        prompt_builder=VideoMotionPromptBuilder(),
        clock=clock,
        monotonic_clock=monotonic,
        owns_client=True,
    )


def _integer_duration(value: float) -> int:
    result = int(value)
    if result != value:
        raise OpenRouterVideoConfigurationError(
            "OpenRouter video duration must be a whole number of seconds"
        )
    return result


def _validate_base_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "openrouter.ai"
        or parsed.path.rstrip("/") != "/api/v1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise OpenRouterVideoConfigurationError(
            "OpenRouter video base URL must be the official HTTPS API"
        )


def _validate_client(client: httpx.AsyncClient) -> None:
    parsed = urlsplit(str(client.base_url))
    if (
        parsed.scheme != "https"
        or parsed.hostname != "openrouter.ai"
        or parsed.username is not None
        or parsed.password is not None
        or client.follow_redirects
    ):
        raise OpenRouterVideoConfigurationError("OpenRouter video HTTP client policy is unsafe")


def _validate_remote_id(value: str) -> None:
    if _REMOTE_ID.fullmatch(value) is None:
        raise OpenRouterVideoInvalidResponseError("remote video job ID is invalid")


def _validate_job_identity(job: OpenRouterVideoJob, expected: str | None = None) -> None:
    _validate_remote_id(job.id)
    if expected is not None and job.id != expected:
        raise OpenRouterVideoInvalidResponseError("remote video job ID changed")


def _validated_remote_path(value: str, job_id: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme or parsed.netloc) and (
        parsed.scheme != "https"
        or parsed.hostname != "openrouter.ai"
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise OpenRouterVideoInvalidResponseError("remote video polling URL host is invalid")
    if parsed.query or parsed.fragment or parsed.path != f"/api/v1/videos/{job_id}":
        raise OpenRouterVideoInvalidResponseError("remote video polling URL path is invalid")
    return parsed.path


def _request_fingerprint(
    *,
    request: VideoClipProviderRequest,
    prompt_sha256: str,
    capability_hash: str,
    publication_id: str,
    aspect_ratio: str,
) -> str:
    payload = {
        "configuration": request.fingerprint,
        "source_image_sha256": request.source_image_sha256,
        "visual_asset_id": request.visual_asset_id,
        "prompt_sha256": prompt_sha256,
        "capability_snapshot_hash": capability_hash,
        "publication_id": publication_id,
        "aspect_ratio": aspect_ratio,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _safe_response_metadata(
    record: RemoteVideoJobRecord, content_sha256: str
) -> dict[str, str | int | bool]:
    if (
        record.remote_job_id is None
        or record.remote_status is None
        or record.submitted_at is None
    ):
        raise OpenRouterVideoInvalidResponseError(
            "completed remote video metadata is incomplete"
        )
    result: dict[str, str | int | bool] = {
        "remote_provider": "openrouter",
        "remote_job_id": record.remote_job_id,
        "remote_status": record.remote_status.value,
        "request_status": record.request_status.value,
        "remote_poll_attempts": record.poll_attempts,
        "remote_content_available": record.remote_content_available,
        "remote_submitted_at": record.submitted_at.isoformat(),
        "pricing_snapshot_at": record.pricing_snapshot_at.isoformat(),
        "estimated_cost_usd": str(record.estimated_cost_usd),
        "pricing_sku": record.pricing_sku,
        "prompt_sha256": record.prompt_sha256,
        "capability_snapshot_hash": record.capability_snapshot_hash,
        "source_publication_id": record.publication_id,
        "publication_provider": record.publication_provider,
        "provider_request_fingerprint": record.provider_request_fingerprint,
        "content_sha256": content_sha256,
        "simulated": False,
        "deterministic": False,
    }
    if record.remote_generation_id is not None:
        result["remote_generation_id"] = record.remote_generation_id
    if record.last_polled_at is not None:
        result["remote_last_polled_at"] = record.last_polled_at.isoformat()
    if record.terminal_at is not None:
        result["remote_terminal_at"] = record.terminal_at.isoformat()
    if record.publication_expires_at is not None:
        result["source_publication_expires_at"] = record.publication_expires_at.isoformat()
    if record.reported_cost_usd is not None:
        result["reported_cost_usd"] = str(record.reported_cost_usd)
    if record.reported_model is not None:
        result["reported_model"] = record.reported_model
    if record.submission_http_status is not None:
        result["submission_http_status"] = record.submission_http_status
    return result


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return max(0.0, min(parsed, 300.0))
