"""Durable pre-submission diagnostics for the first controlled Veo contract."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.asset_publishing.publishers.filesystem import (
    FilesystemPublisher,
)
from backend.src.production.video_clip_generation.configuration import (
    VideoClipGenerationConfiguration,
)
from backend.src.production.video_clip_generation.frame_image_publisher import (
    PublishedAssetVideoFrameImagePublisher,
)
from backend.src.production.video_clip_generation.handler import VideoClipGenerationHandler
from backend.src.production.video_clip_generation.manifest_writer import (
    LocalVideoClipManifestWriter,
)
from backend.src.production.video_clip_generation.prompt_builder import (
    VideoMotionPromptBuilder,
)
from backend.src.production.video_clip_generation.providers.openrouter_capabilities import (
    OpenRouterVideoModelCapabilityResolver,
)
from backend.src.production.video_clip_generation.providers.openrouter_models import (
    OpenRouterVideoProviderConfiguration,
    OpenRouterVideoRequestStatus,
    RemoteVideoJobRecord,
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
from backend.src.production.video_clip_generation.serialization import (
    deserialize_video_clip_manifest,
)
from backend.tests.unit.production.video_clip_generation.conftest import (
    JOB_ID,
    NOW,
    command_context,
    durable_source,
)
from backend.tests.unit.production.video_clip_generation.test_reader_and_handler import (
    FakeReader,
    video_store,
)

MODEL = "google/veo-3.1-lite"


class RecordingRemoteStore(InMemoryRemoteVideoJobStore):
    def __init__(self) -> None:
        super().__init__()
        self.created: list[RemoteVideoJobRecord] = []

    async def create(self, record: RemoteVideoJobRecord) -> None:
        self.created.append(record)
        await super().create(record)


def capability(
    *,
    durations: list[int] | None = None,
    resolutions: list[str] | None = None,
    aspects: list[str] | None = None,
    pricing: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "id": MODEL,
        "supported_durations": durations if durations is not None else [8, 4, 6],
        "supported_resolutions": (
            resolutions if resolutions is not None else ["720p", "1080p"]
        ),
        "supported_aspect_ratios": (
            aspects if aspects is not None else ["16:9", "9:16"]
        ),
        "supported_frame_images": ["first_frame", "last_frame"],
        "generate_audio": True,
        "allowed_passthrough_parameters": [
            "personGeneration",
            "aspectRatio",
            "negativePrompt",
            "conditioningScale",
            "enhancePrompt",
        ],
        "pricing_skus": (
            pricing
            if pricing is not None
            else {"duration_seconds_without_audio_720p": "0.03"}
        ),
        "hugging_face_id": None,
        "provider_catalog_metadata": {"ignored": True},
    }


async def execute_case(
    tmp_path,
    *,
    discovery_status: int = 200,
    capabilities: list[dict[str, object]] | None = None,
    source_metadata: dict[str, object] | None = None,
) -> tuple[Any, Any, dict[str, int], RecordingRemoteStore]:
    source, _, _ = await durable_source(tmp_path, width=576, height=1024)
    provenance = source_metadata or {
        "simulated": False,
        "provider": "openrouter",
        "model": "google/gemini-3.1-flash-lite-image",
    }
    source = source.model_copy(
        update={
            "source_images": tuple(
                image.model_copy(update={"metadata": provenance})
                for image in source.source_images
            )
        }
    )
    counts = {"discovery": 0, "post": 0}

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/videos/models":
            counts["discovery"] += 1
            return httpx.Response(
                discovery_status,
                json={"data": capabilities if capabilities is not None else [capability()]},
                request=request,
            )
        if request.method == "POST" and request.url.path == "/api/v1/videos":
            counts["post"] += 1
            return httpx.Response(400, json={"error": "fake stop after prepared"}, request=request)
        raise AssertionError(f"unexpected fake request: {request.method} {request.url.path}")

    publication = FilesystemPublisher(
        public_root=tmp_path / "published-frames",
        public_base_url="https://frames.example.com",
        max_asset_bytes=1_000_000,
        clock=lambda: NOW,
    )
    frames = PublishedAssetVideoFrameImagePublisher(
        publisher=publication,
        lifetime_seconds=900,
        clock=lambda: NOW,
    )
    jobs = RecordingRemoteStore()
    provider_configuration = OpenRouterVideoProviderConfiguration(
        model=MODEL,
        resolution="720p",
        max_estimated_cost_usd=Decimal("0.20"),
        allow_billable_requests=True,
        max_requests_per_job=1,
        poll_interval_seconds=0.01,
        max_poll_seconds=60,
        max_poll_attempts=5,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport_handler),
        base_url="https://openrouter.ai",
        follow_redirects=False,
    ) as client:
        provider = OpenRouterVideoClipGenerationProvider(
            api_key="or-test-key-never-real",
            configuration=provider_configuration,
            client=client,
            capability_resolver=OpenRouterVideoModelCapabilityResolver(
                client=client,
                max_response_bytes=100_000,
                cache_ttl_seconds=60,
                monotonic=lambda: 0,
            ),
            frame_publisher=frames,
            remote_job_store=jobs,
            cost_policy=BillableVideoGenerationPolicy(
                allow_billable_requests=True,
                max_estimated_cost_usd=Decimal("0.20"),
            ),
            polling_policy=OpenRouterVideoPollingPolicy(
                interval_seconds=0.01,
                max_seconds=60,
                max_attempts=5,
                monotonic=lambda: 0,
                sleeper=lambda _: _no_sleep(),
                jitter=lambda _: 0,
            ),
            prompt_builder=VideoMotionPromptBuilder(),
            clock=lambda: NOW,
            monotonic_clock=lambda: 0,
        )
        component = VideoClipGenerationHandler(
            manifest_reader=FakeReader(source),
            provider=provider,
            binary_store=video_store(tmp_path),
            manifest_writer=LocalVideoClipManifestWriter(
                tmp_path, max_manifest_bytes=500_000
            ),
            configuration=VideoClipGenerationConfiguration(
                provider="openrouter",
                model=MODEL,
                resolution="720p",
                generate_audio=False,
                duration_seconds=4,
                max_duration_seconds=4,
                frame_rate=24,
            ),
            clock=lambda: NOW,
        )
        command, context = command_context()
        output = await component.execute(command, context)
    manifest_path = (
        tmp_path
        / "production"
        / str(JOB_ID)
        / "generating_video_clips"
        / "attempt-1"
        / "video-clip-generation-manifest.json"
    )
    manifest = deserialize_video_clip_manifest(manifest_path.read_bytes())
    return output, manifest.entries[0], counts, jobs


async def _no_sleep() -> None:
    return None


@pytest.mark.parametrize(
    "source_metadata",
    [
        {"simulated": True},
        {"provider": "orion-simulated"},
        {"model": "simulated-image-v1"},
    ],
)
@pytest.mark.asyncio
async def test_simulated_source_is_durably_rejected_before_any_remote_request(
    tmp_path,
    source_metadata: dict[str, object],
) -> None:
    output, entry, counts, jobs = await execute_case(
        tmp_path,
        source_metadata=source_metadata,
    )
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert output.result.error_code == "video_clip_source_invalid"
    assert output.result.metadata["diagnostic_code"] == (
        "simulated_source_asset_not_billable"
    )
    assert entry.error_code == "video_clip_source_invalid"
    assert entry.metadata["phase"] == "source_validation"
    assert entry.metadata["diagnostic_code"] == "simulated_source_asset_not_billable"
    durable_field = {
        "simulated": "source_asset_simulated",
        "provider": "source_asset_provider",
        "model": "source_asset_model",
    }
    for key, value in source_metadata.items():
        assert entry.metadata[durable_field[key]] == value
    assert counts == {"discovery": 0, "post": 0}
    assert jobs.created == []


@pytest.mark.asyncio
async def test_a_capability_endpoint_failure_is_durable_and_never_submits(
    tmp_path,
) -> None:
    output, entry, counts, jobs = await execute_case(tmp_path, discovery_status=503)
    assert output.result.error_code == "video_clip_capability_error"
    assert entry.error_code == "video_clip_capability_error"
    assert entry.metadata["phase"] == "capability_discovery"
    assert entry.metadata["diagnostic_code"] == "capability_http_error"
    assert entry.metadata["capability_endpoint_status"] == 503
    assert entry.metadata["requested_model"] == MODEL
    assert entry.metadata["requested_duration_seconds"] == 4
    assert entry.metadata["requested_resolution"] == "720p"
    assert entry.metadata["requested_aspect_ratio"] == "9:16"
    assert entry.metadata["generate_audio"] is False
    assert "publication_id" not in entry.metadata
    assert output.result.metadata["diagnostic_code"] == "capability_http_error"
    serialized = repr(entry.metadata) + repr(output.result.metadata)
    assert "or-test-key-never-real" not in serialized
    assert "Authorization" not in serialized
    assert "https://" not in serialized
    assert counts == {"discovery": 1, "post": 0}
    assert jobs.created == []


@pytest.mark.asyncio
async def test_b_missing_model_is_durable_and_never_submits(tmp_path) -> None:
    _, entry, counts, jobs = await execute_case(tmp_path, capabilities=[])
    assert entry.error_code == "video_clip_capability_error"
    assert entry.metadata["diagnostic_code"] == "capability_model_not_found"
    assert entry.metadata["capability_model_found"] is False
    assert counts["post"] == 0
    assert jobs.created == []


@pytest.mark.asyncio
async def test_c_unsupported_duration_is_durable_and_never_submits(
    tmp_path,
) -> None:
    _, entry, counts, _ = await execute_case(
        tmp_path, capabilities=[capability(durations=[3])]
    )
    assert entry.metadata["diagnostic_code"] == "capability_duration_unsupported"
    assert entry.metadata["requested_duration_seconds"] == 4
    assert counts["post"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("updates", "diagnostic"),
    [
        ({"resolutions": ["1080p"]}, "capability_resolution_unsupported"),
        ({"aspects": ["16:9"]}, "capability_aspect_ratio_unsupported"),
    ],
)
async def test_d_resolution_or_ratio_is_durable_and_never_submits(
    tmp_path, updates, diagnostic
) -> None:
    _, entry, counts, _ = await execute_case(
        tmp_path, capabilities=[capability(**updates)]
    )
    assert entry.error_code == "video_clip_capability_error"
    assert entry.metadata["diagnostic_code"] == diagnostic
    assert entry.metadata["requested_resolution"] == "720p"
    assert entry.metadata["requested_aspect_ratio"] == "9:16"
    assert counts["post"] == 0


@pytest.mark.asyncio
async def test_e_missing_pricing_is_durable_and_never_submits(tmp_path) -> None:
    _, entry, counts, _ = await execute_case(
        tmp_path, capabilities=[capability(pricing={})]
    )
    assert entry.error_code == "video_clip_pricing_error"
    assert entry.metadata["diagnostic_code"] == "pricing_sku_missing"
    assert entry.metadata["max_estimated_cost_usd"] == "0.20"
    assert counts["post"] == 0


@pytest.mark.asyncio
async def test_f_estimate_above_limit_is_durable_and_never_submits(
    tmp_path,
) -> None:
    _, entry, counts, _ = await execute_case(
        tmp_path,
        capabilities=[
            capability(pricing={"duration_seconds_without_audio_720p": "0.06"})
        ],
    )
    assert entry.error_code == "video_clip_cost_policy"
    assert entry.metadata["diagnostic_code"] == "cost_limit_exceeded"
    assert entry.metadata["pricing_sku"] == "duration_seconds_without_audio_720p"
    assert entry.metadata["estimated_cost_usd"] == "0.24"
    assert entry.metadata["max_estimated_cost_usd"] == "0.20"
    assert counts["post"] == 0


@pytest.mark.asyncio
async def test_g_valid_contract_reaches_prepared_exactly_once(tmp_path) -> None:
    output, _, counts, jobs = await execute_case(tmp_path)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert counts == {"discovery": 1, "post": 1}
    assert len(jobs.created) == 1
    assert jobs.created[0].request_status is OpenRouterVideoRequestStatus.PREPARED
    assert jobs.created[0].model == MODEL
    assert jobs.created[0].estimated_cost_usd == Decimal("0.12")
    assert jobs.created[0].pricing_sku == "duration_seconds_without_audio_720p"
    assert jobs.created[0].requested_duration_seconds == 4
    assert jobs.created[0].requested_resolution == "720p"
    assert jobs.created[0].requested_aspect_ratio == "9:16"
    assert jobs.created[0].generate_audio is False


@pytest.mark.asyncio
async def test_invalid_pricing_shape_is_classified_without_provider_body(
    tmp_path,
) -> None:
    broken = capability()
    broken["pricing_skus"] = {"per-video-second-720p": "not-a-decimal"}
    _, entry, counts, _ = await execute_case(tmp_path, capabilities=[broken])
    assert entry.error_code == "video_clip_pricing_error"
    assert entry.metadata["phase"] == "pricing_discovery"
    assert entry.metadata["diagnostic_code"] == "pricing_invalid"
    assert counts["post"] == 0
    assert "not-a-decimal" not in repr(entry.metadata)
