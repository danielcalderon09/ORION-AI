"""MockTransport-only OpenRouter submit, polling, download, and recovery tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import pytest

from backend.src.production.video_clip_generation.exceptions import (
    OpenRouterVideoAuthenticationError,
    OpenRouterVideoConfigurationError,
    OpenRouterVideoContentTypeError,
    OpenRouterVideoCostPolicyError,
    OpenRouterVideoDownloadError,
    OpenRouterVideoInsufficientCreditsError,
    OpenRouterVideoInvalidRequestError,
    OpenRouterVideoPermissionError,
    OpenRouterVideoRateLimitError,
    OpenRouterVideoRemoteCancelledError,
    OpenRouterVideoRemoteExpiredError,
    OpenRouterVideoRemoteFailedError,
    OpenRouterVideoResponseTooLargeError,
    OpenRouterVideoServerError,
    OpenRouterVideoUncertainSubmissionError,
    RemoteVideoJobStoreError,
    VideoFramePublicationUnavailableError,
)
from backend.src.production.video_clip_generation.frame_image_publisher import (
    InMemoryVideoFrameImagePublisher,
)
from backend.src.production.video_clip_generation.ports import VideoClipProviderRequest
from backend.src.production.video_clip_generation.prompt_builder import (
    VideoMotionPromptBuilder,
)
from backend.src.production.video_clip_generation.providers.openrouter_capabilities import (
    OpenRouterVideoModelCapabilityResolver,
)
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    OpenRouterVideoProviderConfiguration,
    OpenRouterVideoRequestStatus,
)
from backend.src.production.video_clip_generation.providers.openrouter_provider import (
    OpenRouterVideoClipGenerationProvider,
)
from backend.src.production.video_clip_generation.remote_job_store import (
    InMemoryRemoteVideoJobStore,
)
from backend.src.production.video_clip_generation.remote_jobs import (
    BillableVideoGenerationPolicy,
    OpenRouterVideoPollingPolicy,
)
from backend.tests.unit.production.video_clip_generation.conftest import NOW
from backend.tests.unit.production.video_clip_generation.test_openrouter_contracts import (
    openrouter_request,
)

FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom\x00\x00\x00\x08free"


def models_body() -> dict[str, object]:
    return {
        "data": [
            {
                "id": "test/video-model",
                "canonical_slug": "test/video-model",
                "supported_durations": [4],
                "supported_resolutions": ["720p"],
                "supported_aspect_ratios": ["1:1"],
                "supported_sizes": ["720x720"],
                "supported_frame_images": ["first_frame"],
                "generate_audio": True,
                "seed": None,
                "allowed_passthrough_parameters": [],
                "pricing_skus": {"per-video-second": "0.01"},
            }
        ]
    }


async def no_sleep(_: float) -> None:
    return None


def provider_for(
    client: httpx.AsyncClient,
    *,
    store: InMemoryRemoteVideoJobStore | None = None,
    publisher: InMemoryVideoFrameImagePublisher | None = None,
    max_attempts: int = 5,
    max_video_bytes: int = 1_000_000,
    sleeper: Callable[[float], Awaitable[None]] = no_sleep,
    max_requests_per_job: int = 1,
    max_estimated_cost_usd: Decimal = Decimal("1"),
    max_estimated_job_cost_usd: Decimal = Decimal("1"),
) -> tuple[
    OpenRouterVideoClipGenerationProvider,
    InMemoryRemoteVideoJobStore,
    InMemoryVideoFrameImagePublisher,
]:
    jobs = store or InMemoryRemoteVideoJobStore()
    frames = publisher or InMemoryVideoFrameImagePublisher(clock=lambda: NOW)
    configuration = OpenRouterVideoProviderConfiguration(
        model="test/video-model",
        resolution="720p",
        max_estimated_cost_usd=max_estimated_cost_usd,
        allow_billable_requests=True,
        poll_interval_seconds=0.01,
        max_poll_seconds=60,
        max_poll_attempts=max_attempts,
        max_video_bytes=max_video_bytes,
        max_requests_per_job=max_requests_per_job,
        max_estimated_job_cost_usd=max_estimated_job_cost_usd,
    )
    polling = OpenRouterVideoPollingPolicy(
        interval_seconds=0.01,
        max_seconds=60,
        max_attempts=max_attempts,
        monotonic=lambda: 0.0,
        sleeper=sleeper,
        jitter=lambda attempt: 0.0,
    )
    return (
        OpenRouterVideoClipGenerationProvider(
            api_key="or-test-key-never-real",
            configuration=configuration,
            client=client,
            capability_resolver=OpenRouterVideoModelCapabilityResolver(
                client=client,
                max_response_bytes=2_000_000,
                cache_ttl_seconds=3600,
                monotonic=lambda: 0.0,
            ),
            frame_publisher=frames,
            remote_job_store=jobs,
            cost_policy=BillableVideoGenerationPolicy(
                allow_billable_requests=True,
                max_estimated_cost_usd=max_estimated_cost_usd,
            ),
            polling_policy=polling,
            prompt_builder=VideoMotionPromptBuilder(),
            clock=lambda: NOW,
            monotonic_clock=lambda: 0.0,
        ),
        jobs,
        frames,
    )


def multi_scene_models_body() -> dict[str, object]:
    item = models_body()["data"][0]
    assert isinstance(item, dict)
    return {
        "data": [
            {
                **item,
                "supported_durations": [4, 6, 8],
                "pricing_skus": {
                    "duration_seconds_without_audio_720p": "0.03",
                },
            }
        ]
    }


def multi_scene_requests() -> tuple[VideoClipProviderRequest, ...]:
    base = openrouter_request()
    return tuple(
        base.model_copy(
            update={
                "visual_asset_id": f"asset-s{index:03d}-q001-v001",
                "scene_id": f"scene-{index:03d}",
                "shot_id": f"scene-{index:03d}-shot-001",
                "visual_intent_sha256": hashlib.sha256(
                    f"scene-{index:03d}-shot-001".encode()
                ).hexdigest(),
                "source_image_artifact_id": UUID(
                    f"40000000-0000-4000-8000-{index:012d}"
                ),
                "duration_seconds": duration,
            }
        )
        for index, duration in enumerate((4.0, 5.0, 6.0), start=1)
    )


def audio_first_two_scene_requests() -> tuple[VideoClipProviderRequest, ...]:
    return tuple(
        request.model_copy(update={"duration_seconds": duration})
        for request, duration in zip(
            multi_scene_requests()[:2],
            (4.25, 5.0),
            strict=True,
        )
    )


@pytest.mark.asyncio
async def test_audio_first_aggregate_budget_rejects_before_first_video_post() -> None:
    observations: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observations.append((request.method, request.url.path))
        assert request.method == "GET"
        return response(request, 200, multi_scene_models_body())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, jobs, _ = provider_for(
            client,
            max_requests_per_job=2,
            max_estimated_job_cost_usd=Decimal("0.25"),
        )
        with pytest.raises(OpenRouterVideoCostPolicyError) as captured:
            await provider.preflight_job(audio_first_two_scene_requests())

    assert captured.value.diagnostic_code == "aggregate_cost_limit_exceeded"
    assert captured.value.diagnostic_metadata["estimated_job_cost_usd"] == "0.36"
    assert not jobs.records
    assert all(method != "POST" for method, _ in observations)


@pytest.mark.asyncio
async def test_audio_first_accepted_budget_selects_six_second_clips() -> None:
    observations: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observations.append((request.method, request.url.path))
        assert request.method == "GET"
        return response(request, 200, multi_scene_models_body())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, jobs, _ = provider_for(
            client,
            max_requests_per_job=2,
            max_estimated_job_cost_usd=Decimal("0.40"),
        )
        planned = await provider.preflight_job(audio_first_two_scene_requests())

    assert tuple(request.duration_seconds for request in planned.requests) == (6.0, 6.0)
    assert Decimal("0.03") * sum(
        Decimal(str(request.duration_seconds)) for request in planned.requests
    ) == Decimal("0.36")
    assert not jobs.records
    assert observations == [("GET", "/api/v1/videos/models")]


@pytest.mark.asyncio
async def test_persisted_purchase_plan_drift_fails_before_video_post() -> None:
    observations: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observations.append((request.method, request.url.path))
        assert request.method == "GET"
        return response(request, 200, multi_scene_models_body())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, jobs, _ = provider_for(
            client,
            max_requests_per_job=2,
            max_estimated_job_cost_usd=Decimal("0.40"),
        )
        requests = audio_first_two_scene_requests()
        planned = await provider.preflight_job(requests)
        changed = (
            requests[0].model_copy(update={"source_image_sha256": "f" * 64}),
            requests[1],
        )
        with pytest.raises(OpenRouterVideoConfigurationError) as captured:
            await provider.preflight_job(changed, existing_plan=planned.purchase_plan)

    assert captured.value.diagnostic_code == "purchase_plan_drift"
    assert not jobs.records
    assert all(method != "POST" for method, _ in observations)


@pytest.mark.asyncio
async def test_multi_scene_preflight_selects_durations_and_authorizes_aggregate() -> None:
    observations: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observations.append((request.method, request.url.path))
        assert request.method == "GET"
        return response(request, 200, multi_scene_models_body())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(
            client,
            max_requests_per_job=3,
            max_estimated_job_cost_usd=Decimal("0.48"),
        )
        planned = await provider.preflight_job(multi_scene_requests())

    assert tuple(item.duration_seconds for item in planned.requests) == (4.0, 6.0, 6.0)
    assert Decimal("0.03") * sum(
        Decimal(str(item.duration_seconds)) for item in planned.requests
    ) == Decimal("0.48")
    assert observations == [("GET", "/api/v1/videos/models")]


@pytest.mark.asyncio
async def test_multi_scene_aggregate_budget_blocks_before_first_post() -> None:
    observations: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observations.append((request.method, request.url.path))
        return response(request, 200, multi_scene_models_body())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, jobs, _ = provider_for(
            client,
            max_requests_per_job=3,
            max_estimated_job_cost_usd=Decimal("0.40"),
        )
        with pytest.raises(OpenRouterVideoCostPolicyError) as error:
            await provider.preflight_job(multi_scene_requests())

    assert error.value.diagnostic_code == "aggregate_cost_limit_exceeded"
    assert not jobs.records
    assert all(method != "POST" for method, _ in observations)


@pytest.mark.asyncio
async def test_multi_scene_simulated_source_blocks_all_before_discovery_or_post() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected network request: {request.method}")

    requests = list(multi_scene_requests())
    requests[1] = requests[1].model_copy(
        update={"source_metadata": {"simulated": True}}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, jobs, _ = provider_for(client, max_requests_per_job=3)
        with pytest.raises(OpenRouterVideoConfigurationError) as error:
            await provider.preflight_job(tuple(requests))

    assert error.value.diagnostic_code == "simulated_source_asset_not_billable"
    assert not jobs.records


def response(
    request: httpx.Request,
    status: int,
    payload: object | None = None,
    *,
    content: bytes | None = None,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    merged = {"content-type": content_type, **(headers or {})}
    if content is not None:
        return httpx.Response(status, content=content, headers=merged, request=request)
    return httpx.Response(status, json=payload, headers=merged, request=request)


def successful_transport(
    observations: list[tuple[str, str, dict[str, Any] | None]],
    *,
    poll_states: list[str] | None = None,
    content: bytes = FAKE_MP4,
    content_type: str = "video/mp4",
    models_payload: dict[str, object] | None = None,
) -> httpx.MockTransport:
    states = list(poll_states or ["completed"])

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "openrouter.ai"
        body = json.loads(request.content) if request.content else None
        observations.append((request.method, request.url.path, body))
        if request.url.path == "/api/v1/videos/models":
            return response(request, 200, models_payload or models_body())
        if request.method == "POST" and request.url.path == "/api/v1/videos":
            return response(
                request,
                202,
                {
                    "id": "job-abc",
                    "generation_id": "gen-xyz",
                    "polling_url": "/api/v1/videos/job-abc",
                    "status": "pending",
                },
            )
        if request.url.path == "/api/v1/videos/job-abc/content":
            return response(
                request,
                200,
                content=content,
                content_type=content_type,
            )
        if request.url.path == "/api/v1/videos/job-abc":
            status = states.pop(0) if len(states) > 1 else states[0]
            payload: dict[str, object] = {
                "id": "job-abc",
                "generation_id": "gen-xyz",
                "polling_url": "/api/v1/videos/job-abc",
                "status": status,
            }
            if status == "completed":
                payload["usage"] = {"cost": 0.04, "is_byok": False}
                payload["unsigned_urls"] = ["https://untrusted.example.test/video?signature=secret"]
            if status in {"failed", "cancelled", "expired"}:
                payload["error"] = "redacted remote failure"
            return response(request, 200, payload)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_mock_multi_scene_generation_reaches_three_unique_prepared_records() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    async with httpx.AsyncClient(
        transport=successful_transport(
            observations,
            models_payload=multi_scene_models_body(),
        ),
        base_url="https://openrouter.ai",
        follow_redirects=False,
    ) as client:
        provider, jobs, _ = provider_for(
            client,
            max_requests_per_job=3,
            max_estimated_job_cost_usd=Decimal("0.48"),
        )
        planned = await provider.preflight_job(multi_scene_requests())
        for request in planned.requests:
            await provider.generate_clip(request)

    records = tuple(jobs.records.values())
    assert len(records) == 3
    assert len({item.visual_asset_id for item in records}) == 3
    assert len({item.provider_request_fingerprint for item in records}) == 3
    assert tuple(item.requested_duration_seconds for item in records) == (4, 6, 6)
    assert tuple(item.estimated_cost_usd for item in records) == (
        Decimal("0.12"),
        Decimal("0.18"),
        Decimal("0.18"),
    )
    assert all(
        item.pricing_sku == "duration_seconds_without_audio_720p" for item in records
    )
    assert sum(
        method == "POST" and path == "/api/v1/videos"
        for method, path, _ in observations
    ) == 3


@pytest.mark.asyncio
async def test_audio_first_accepted_budget_creates_two_six_second_remote_jobs() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    async with httpx.AsyncClient(
        transport=successful_transport(
            observations,
            models_payload=multi_scene_models_body(),
        ),
        base_url="https://openrouter.ai",
        follow_redirects=False,
    ) as client:
        provider, jobs, _ = provider_for(
            client,
            max_requests_per_job=2,
            max_estimated_job_cost_usd=Decimal("0.40"),
        )
        planned = await provider.preflight_job(audio_first_two_scene_requests())
        for request in planned.requests:
            await provider.generate_clip(request)

    records = tuple(jobs.records.values())
    assert len(records) == 2
    assert tuple(record.requested_duration_seconds for record in records) == (6, 6)
    assert tuple(record.estimated_cost_usd for record in records) == (
        Decimal("0.18"),
        Decimal("0.18"),
    )
    assert sum(
        method == "POST" and path == "/api/v1/videos"
        for method, path, _ in observations
    ) == 2


@pytest.mark.asyncio
async def test_full_submit_poll_download_request_is_closed_and_safe() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    async with httpx.AsyncClient(
        transport=successful_transport(
            observations, poll_states=["pending", "in_progress", "completed"]
        ),
        base_url="https://openrouter.ai",
        follow_redirects=False,
    ) as client:
        provider, jobs, frames = provider_for(client)
        result = await provider.generate_clip(openrouter_request())
        assert result.provider == "openrouter"
        assert result.clips[0].content == FAKE_MP4
        assert result.cost_usd == Decimal("0.04")
        assert result.metadata["remote_status"] == "completed"
        assert frames.calls == 1
        record = next(iter(jobs.records.values()))
        assert record.remote_job_id == "job-abc"
        assert record.poll_attempts == 3
        assert record.remote_status.value == "completed"
        assert record.reported_cost_usd == Decimal("0.04")
        assert all(
            "untrusted.example" not in json.dumps(item.model_dump(mode="json"))
            for item in jobs.records.values()
        )
        submit = next(item for item in observations if item[:2] == ("POST", "/api/v1/videos"))
        body = submit[2]
        assert body is not None
        assert set(body) == {
            "model",
            "prompt",
            "duration",
            "resolution",
            "aspect_ratio",
            "generate_audio",
            "frame_images",
        }
        assert body["generate_audio"] is False
        assert body["frame_images"][0]["frame_type"] == "first_frame"
        assert all(value is not None for value in body.values())
        assert "provider" not in body
        assert "input_references" not in body
        assert "callback_url" not in body
        assert observations[-1][:2] == (
            "GET",
            "/api/v1/videos/job-abc/content",
        )
        assert "url" not in result.model_dump(mode="json")
        assert "content" not in result.model_dump(mode="json")["clips"][0]
        await provider.close()
        await provider.close()


@pytest.mark.asyncio
async def test_remote_completed_recovery_skips_publish_discovery_and_submit() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    store = InMemoryRemoteVideoJobStore()
    publisher = InMemoryVideoFrameImagePublisher(clock=lambda: NOW)
    async with httpx.AsyncClient(
        transport=successful_transport(observations),
        base_url="https://openrouter.ai",
    ) as client:
        first, _, _ = provider_for(client, store=store, publisher=publisher)
        await first.generate_clip(openrouter_request())
        first_calls = len(observations)
        second, _, _ = provider_for(client, store=store, publisher=publisher)
        result = await second.generate_clip(openrouter_request())
        assert result.finish_reason == "completed"
        recovery_calls = observations[first_calls:]
        assert recovery_calls == [("GET", "/api/v1/videos/job-abc/content", None)]
        assert publisher.calls == 1


@pytest.mark.asyncio
async def test_new_attempt_reuses_completed_remote_job_without_submit() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    store = InMemoryRemoteVideoJobStore()
    publisher = InMemoryVideoFrameImagePublisher(clock=lambda: NOW)
    async with httpx.AsyncClient(
        transport=successful_transport(observations),
        base_url="https://openrouter.ai",
    ) as client:
        first, _, _ = provider_for(client, store=store, publisher=publisher)
        await first.generate_clip(openrouter_request())
        first_calls = len(observations)

        retry, _, _ = provider_for(client, store=store, publisher=publisher)
        result = await retry.generate_clip(openrouter_request(attempt_number=2))

        assert result.finish_reason == "completed"
        assert observations[first_calls:] == [
            ("GET", "/api/v1/videos/job-abc/content", None)
        ]
        assert publisher.calls == 1
        assert [item[0] for item in observations].count("POST") == 1


@pytest.mark.asyncio
async def test_new_attempt_rejects_changed_request_fingerprint_without_submit() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    store = InMemoryRemoteVideoJobStore()
    async with httpx.AsyncClient(
        transport=successful_transport(observations),
        base_url="https://openrouter.ai",
    ) as client:
        first, _, _ = provider_for(client, store=store)
        await first.generate_clip(openrouter_request())
        submit_count = [item[0] for item in observations].count("POST")

        retry, _, _ = provider_for(client, store=store)
        changed = openrouter_request(attempt_number=2).model_copy(
            update={"fingerprint": "f" * 64}
        )
        with pytest.raises(OpenRouterVideoConfigurationError):
            await retry.generate_clip(changed)

        assert [item[0] for item in observations].count("POST") == submit_count


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, OpenRouterVideoInvalidRequestError),
        (401, OpenRouterVideoAuthenticationError),
        (402, OpenRouterVideoInsufficientCreditsError),
        (403, OpenRouterVideoPermissionError),
        (404, OpenRouterVideoInvalidRequestError),
        (409, OpenRouterVideoInvalidRequestError),
        (422, OpenRouterVideoInvalidRequestError),
        (429, OpenRouterVideoRateLimitError),
        (500, OpenRouterVideoServerError),
        (502, OpenRouterVideoServerError),
        (503, OpenRouterVideoServerError),
    ],
)
@pytest.mark.asyncio
async def test_submit_http_classification(status: int, expected: type[Exception]) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return response(request, 200, models_body())
        return response(request, status, {"error": {"message": "redacted"}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client)
        with pytest.raises(expected):
            await provider.generate_clip(openrouter_request())


@pytest.mark.asyncio
async def test_submit_http_error_preserves_only_safe_bounded_diagnostics() -> None:
    secret = "https://published.example.test/frame.jpg?token=must-not-persist"
    bearer = "Bearer secret-value"
    prompt_like = "private cinematic prompt must-not-persist"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return response(request, 200, models_body())
        return response(
            request,
            400,
            {
                "error": {
                    "code": "reference_image_unreachable",
                    "message": (
                        f"Could not fetch the frame image URL {secret}; "
                        f"{bearer}; {prompt_like}"
                    ),
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client)
        with pytest.raises(OpenRouterVideoInvalidRequestError) as captured:
            await provider.generate_clip(openrouter_request())

    error = captured.value
    assert error.diagnostic_phase == "provider_submit"
    assert error.diagnostic_code == "video_reference_asset_invalid"
    assert error.diagnostic_metadata["provider_http_status"] == 400
    assert error.diagnostic_metadata["provider_operation"] == "submit"
    assert error.diagnostic_metadata["provider_error_code"] == (
        "reference_image_unreachable"
    )
    assert error.diagnostic_metadata["openrouter_error_code"] == (
        "reference_image_unreachable"
    )
    assert error.diagnostic_metadata["provider_error_reason"] == (
        "reference_asset_unreachable"
    )
    assert error.diagnostic_metadata["provider_error_body_bytes"] > 0
    assert len(error.diagnostic_metadata["provider_error_body_sha256"]) == 64
    assert secret not in repr(error.diagnostic_metadata)
    assert bearer not in repr(error.diagnostic_metadata)
    assert prompt_like not in repr(error.diagnostic_metadata)


@pytest.mark.asyncio
async def test_submit_unknown_http_error_uses_fail_closed_leaf_code() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return response(request, 200, models_body())
        return response(request, 400, {"error": {"message": "redacted"}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client)
        with pytest.raises(OpenRouterVideoInvalidRequestError) as captured:
            await provider.generate_clip(openrouter_request())

    assert captured.value.diagnostic_code == "video_provider_http_error"
    assert captured.value.diagnostic_phase == "provider_submit"
    assert captured.value.diagnostic_metadata["provider_error_reason"] == (
        "invalid_request"
    )


@pytest.mark.parametrize(
    ("message", "expected_code", "expected_reason"),
    [
        (
            "Could not fetch the first frame image URL",
            "video_reference_asset_invalid",
            "reference_asset_unreachable",
        ),
        ("Requested duration is unsupported", "video_duration_invalid", "invalid_duration"),
        (
            "Requested aspect ratio is unsupported",
            "video_request_dimensions_invalid",
            "invalid_dimensions",
        ),
        ("Requested model is unavailable", "video_provider_model_invalid", "invalid_model"),
        (
            "Video routing is incompatible with ZDR",
            "video_provider_zdr_incompatible",
            "zdr_incompatible",
        ),
    ],
)
@pytest.mark.asyncio
async def test_submit_http_error_has_stable_safe_leaf_classification(
    message: str,
    expected_code: str,
    expected_reason: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return response(request, 200, models_body())
        return response(request, 400, {"error": {"message": message}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client)
        with pytest.raises(OpenRouterVideoInvalidRequestError) as captured:
            await provider.generate_clip(openrouter_request())

    assert captured.value.diagnostic_code == expected_code
    assert captured.value.diagnostic_metadata["provider_error_reason"] == expected_reason


@pytest.mark.parametrize(
    ("error_type", "expected_code", "expected_reason"),
    [
        ("image_not_found", "video_reference_asset_invalid", "reference_asset_unreachable"),
        ("invalid_image", "video_reference_asset_invalid", "reference_asset_invalid"),
        (
            "unsupported_image_format",
            "video_reference_asset_invalid",
            "reference_asset_invalid",
        ),
        (
            "payment_required",
            "video_provider_insufficient_credits",
            "insufficient_credits",
        ),
        (
            "permission_denied",
            "video_provider_permission_denied",
            "permission_denied",
        ),
        (
            "content_policy_violation",
            "video_provider_content_policy",
            "content_policy",
        ),
        (
            "provider_unavailable",
            "video_provider_unavailable",
            "provider_unavailable",
        ),
    ],
)
@pytest.mark.asyncio
async def test_submit_prefers_safe_structured_error_type(
    error_type: str,
    expected_code: str,
    expected_reason: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return response(request, 200, models_body())
        return response(
            request,
            400,
            {
                "error": {
                    "code": 400,
                    "message": "Bad request",
                    "metadata": {
                        "error_type": error_type,
                        "provider_code": "invalid_image_reference",
                    },
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client)
        with pytest.raises(OpenRouterVideoInvalidRequestError) as captured:
            await provider.generate_clip(openrouter_request())

    metadata = captured.value.diagnostic_metadata
    assert captured.value.diagnostic_code == expected_code
    assert metadata["openrouter_error_code"] == "400"
    assert metadata["provider_error_code"] == "400"
    assert metadata["openrouter_error_type"] == error_type
    assert metadata["openrouter_provider_code"] == "invalid_image_reference"
    assert metadata["provider_error_reason"] == expected_reason


@pytest.mark.asyncio
async def test_submit_omits_unsafe_structured_diagnostic_values() -> None:
    unsafe = "Bearer secret-value"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return response(request, 200, models_body())
        return response(
            request,
            400,
            {
                "error": {
                    "code": unsafe,
                    "message": "Bad request",
                    "metadata": {
                        "error_type": unsafe,
                        "provider_code": unsafe,
                    },
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client)
        with pytest.raises(OpenRouterVideoInvalidRequestError) as captured:
            await provider.generate_clip(openrouter_request())

    metadata = captured.value.diagnostic_metadata
    assert "openrouter_error_code" not in metadata
    assert "provider_error_code" not in metadata
    assert "openrouter_error_type" not in metadata
    assert "openrouter_provider_code" not in metadata
    assert unsafe not in repr(metadata)


@pytest.mark.asyncio
async def test_specific_provider_code_refines_generic_error_type() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return response(request, 200, models_body())
        return response(
            request,
            400,
            {
                "error": {
                    "code": 400,
                    "message": "Bad request",
                    "metadata": {
                        "error_type": "invalid_request",
                        "provider_code": "invalid_duration",
                    },
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client)
        with pytest.raises(OpenRouterVideoInvalidRequestError) as captured:
            await provider.generate_clip(openrouter_request())

    assert captured.value.diagnostic_code == "video_duration_invalid"
    assert captured.value.diagnostic_metadata["provider_error_reason"] == (
        "invalid_duration"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"id": "job"},
        {
            "id": "job",
            "polling_url": "/api/v1/videos/job",
            "status": "unknown",
        },
        {
            "id": "../job",
            "polling_url": "/api/v1/videos/../job",
            "status": "pending",
        },
        {
            "id": "job",
            "polling_url": "http://openrouter.ai/api/v1/videos/job",
            "status": "pending",
        },
        {
            "id": "job",
            "polling_url": "https://evil.example/api/v1/videos/job",
            "status": "pending",
        },
        {
            "id": "job",
            "polling_url": "/api/v1/videos/job/../other",
            "status": "pending",
        },
        {
            "id": "job",
            "polling_url": "/api/v1/videos/job?token=secret",
            "status": "pending",
        },
        {
            "id": "job",
            "polling_url": "/api/v1/videos/job",
            "status": "failed",
        },
        {
            "id": "job",
            "polling_url": "/api/v1/videos/job",
            "status": "pending",
            "extra": "field",
        },
    ],
)
@pytest.mark.asyncio
async def test_invalid_202_response_is_uncertain(payload: dict[str, object]) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return response(request, 200, models_body())
        return response(request, 202, payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, store, _ = provider_for(client)
        with pytest.raises(OpenRouterVideoUncertainSubmissionError):
            await provider.generate_clip(openrouter_request())
        record = next(iter(store.records.values()))
        assert record.request_status is OpenRouterVideoRequestStatus.UNCERTAIN
        assert record.fresh_submission_permitted is False
        assert record.remote_job_id is None


@pytest.mark.parametrize(
    ("remote_status", "expected"),
    [
        ("failed", OpenRouterVideoRemoteFailedError),
        ("cancelled", OpenRouterVideoRemoteCancelledError),
        ("expired", OpenRouterVideoRemoteExpiredError),
    ],
)
@pytest.mark.asyncio
async def test_remote_terminal_failure_is_typed_and_not_resubmitted(
    remote_status: str, expected: type[Exception]
) -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    store = InMemoryRemoteVideoJobStore()
    async with httpx.AsyncClient(
        transport=successful_transport(observations, poll_states=[remote_status]),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client, store=store)
        with pytest.raises(expected):
            await provider.generate_clip(openrouter_request())
        submits = [item for item in observations if item[0] == "POST"]
        assert len(submits) == 1
        recovered, _, _ = provider_for(client, store=store)
        with pytest.raises(expected):
            await recovered.generate_clip(openrouter_request())
        assert len([item for item in observations if item[0] == "POST"]) == 1


@pytest.mark.parametrize(
    ("content_type", "content", "expected"),
    [
        ("text/html", b"<html>error</html>", OpenRouterVideoContentTypeError),
        ("application/json", b'{"error":"x"}', OpenRouterVideoContentTypeError),
        ("application/xml", b"<error/>", OpenRouterVideoContentTypeError),
        ("image/png", b"\x89PNG", OpenRouterVideoContentTypeError),
        ("video/mp4", b"", OpenRouterVideoDownloadError),
        ("video/mp4", b"not-an-mp4", OpenRouterVideoDownloadError),
    ],
)
@pytest.mark.asyncio
async def test_download_rejects_wrong_or_corrupt_content(
    content_type: str, content: bytes, expected: type[Exception]
) -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    async with httpx.AsyncClient(
        transport=successful_transport(observations, content=content, content_type=content_type),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client)
        with pytest.raises(expected):
            await provider.generate_clip(openrouter_request())


@pytest.mark.asyncio
async def test_download_enforces_incremental_size_limit() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    async with httpx.AsyncClient(
        transport=successful_transport(observations, content=FAKE_MP4 * 10),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client, max_video_bytes=32)
        with pytest.raises(OpenRouterVideoResponseTooLargeError):
            await provider.generate_clip(openrouter_request())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectTimeout("connect"), OpenRouterVideoUncertainSubmissionError),
        (httpx.ConnectError("connect"), OpenRouterVideoUncertainSubmissionError),
        (
            httpx.ReadTimeout("read"),
            OpenRouterVideoUncertainSubmissionError,
        ),
        (
            httpx.WriteTimeout("write"),
            OpenRouterVideoUncertainSubmissionError,
        ),
    ],
)
@pytest.mark.asyncio
async def test_submit_transport_failure_distinguishes_uncertainty(
    error: Exception, expected: type[Exception]
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return response(request, 200, models_body())
        raise error

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, store, _ = provider_for(client)
        with pytest.raises(expected):
            await provider.generate_clip(openrouter_request())
        record = next(iter(store.records.values()))
        assert record.request_status is OpenRouterVideoRequestStatus.UNCERTAIN
        assert record.fresh_submission_permitted is False
        assert record.remote_job_id is None


@pytest.mark.asyncio
async def test_submit_cancellation_is_uncertain_and_never_resubmitted() -> None:
    post_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.url.path.endswith("/models"):
            return response(request, 200, models_body())
        post_count += 1
        raise asyncio.CancelledError

    store = InMemoryRemoteVideoJobStore()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client, store=store)
        with pytest.raises(asyncio.CancelledError):
            await provider.generate_clip(openrouter_request())
        record = next(iter(store.records.values()))
        assert record.request_status is OpenRouterVideoRequestStatus.UNCERTAIN
        assert record.fresh_submission_permitted is False
        with pytest.raises(OpenRouterVideoUncertainSubmissionError):
            await provider.generate_clip(openrouter_request())
        assert post_count == 1


@pytest.mark.asyncio
async def test_paid_submission_limit_blocks_second_visual_without_http() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    async with httpx.AsyncClient(
        transport=successful_transport(observations),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, frames = provider_for(client, max_requests_per_job=1)
        await provider.generate_clip(openrouter_request())
        with pytest.raises(OpenRouterVideoCostPolicyError, match="limit"):
            await provider.generate_clip(
                openrouter_request().model_copy(
                    update={"visual_asset_id": "asset-s001-q002-v001"}
                )
            )
        assert len([item for item in observations if item[0] == "POST"]) == 1
        assert frames.calls == 1


@pytest.mark.asyncio
async def test_polling_is_bounded_without_real_sleep() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    sleeps: list[float] = []

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(
        transport=successful_transport(observations, poll_states=["pending"]),
        base_url="https://openrouter.ai",
    ) as client:
        provider, store, _ = provider_for(client, max_attempts=3, sleeper=sleeper)
        with pytest.raises(OpenRouterVideoUncertainSubmissionError):
            await provider.generate_clip(openrouter_request())
        assert sleeps == [0.01, 0.01, 0.01]
        assert next(iter(store.records.values())).poll_attempts == 3
        assert len([item for item in observations if item[0] == "POST"]) == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_during_poll_sleep() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []

    async def cancelled(_: float) -> None:
        raise asyncio.CancelledError

    async with httpx.AsyncClient(
        transport=successful_transport(observations, poll_states=["pending"]),
        base_url="https://openrouter.ai",
    ) as client:
        provider, store, _ = provider_for(client, sleeper=cancelled)
        with pytest.raises(asyncio.CancelledError):
            await provider.generate_clip(openrouter_request())
        assert len(store.records) == 1
        assert len([item for item in observations if item[0] == "POST"]) == 1


@pytest.mark.asyncio
async def test_restart_after_cancelled_poll_resumes_without_submit() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    store = InMemoryRemoteVideoJobStore()

    async def cancelled(_: float) -> None:
        raise asyncio.CancelledError

    async with httpx.AsyncClient(
        transport=successful_transport(
            observations, poll_states=["completed"]
        ),
        base_url="https://openrouter.ai",
    ) as client:
        first, _, _ = provider_for(client, store=store, sleeper=cancelled)
        with pytest.raises(asyncio.CancelledError):
            await first.generate_clip(openrouter_request())
        submitted_count = len(
            [item for item in observations if item[0] == "POST"]
        )
        recovered, _, _ = provider_for(client, store=store)
        result = await recovered.generate_clip(openrouter_request())
        assert result.finish_reason == "completed"
        assert len([item for item in observations if item[0] == "POST"]) == submitted_count


@pytest.mark.asyncio
async def test_checkpoint_failure_after_202_is_uncertain_and_never_polls() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []

    class FailingStore(InMemoryRemoteVideoJobStore):
        async def checkpoint(self, *, previous, current) -> None:
            if current.remote_job_id is not None:
                raise RemoteVideoJobStoreError("simulated durable failure")
            await super().checkpoint(previous=previous, current=current)

    async with httpx.AsyncClient(
        transport=successful_transport(observations),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client, store=FailingStore())
        with pytest.raises(OpenRouterVideoUncertainSubmissionError):
            await provider.generate_clip(openrouter_request())
        assert [item[0] for item in observations].count("POST") == 1
        assert not any(
            item[:2] == ("GET", "/api/v1/videos/job-abc")
            for item in observations
        )


@pytest.mark.asyncio
async def test_expired_publication_blocks_before_discovery_and_submit() -> None:
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    publisher = InMemoryVideoFrameImagePublisher(
        clock=lambda: NOW, lifetime_seconds=-1
    )
    async with httpx.AsyncClient(
        transport=successful_transport(observations),
        base_url="https://openrouter.ai",
    ) as client:
        provider, _, _ = provider_for(client, publisher=publisher)
        with pytest.raises(VideoFramePublicationUnavailableError):
            await provider.generate_clip(openrouter_request())
        assert observations == []


@pytest.mark.asyncio
async def test_authorization_header_is_sent_but_never_in_contract_repr() -> None:
    headers: list[str] = []
    observations: list[tuple[str, str, dict[str, Any] | None]] = []
    transport = successful_transport(observations)

    async def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("authorization", ""))
        return await transport.handler(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai",
    ) as client:
        provider, store, _ = provider_for(client)
        result = await provider.generate_clip(openrouter_request())
        assert all(value == "Bearer or-test-key-never-real" for value in headers)
        combined = repr(result) + repr(store.records)
        assert "or-test-key-never-real" not in combined
        assert "authorization" not in combined.lower()
