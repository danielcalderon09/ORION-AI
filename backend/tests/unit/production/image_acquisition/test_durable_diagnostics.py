"""Durable, sanitized image-failure diagnostics with fake providers only."""

import asyncio
import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO

import httpx
import pytest
from PIL import Image

from backend.src.production.application.results import StageOutcome
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetIOError,
    BinaryAssetMimeError,
)
from backend.src.production.image_acquisition.configuration import (
    ImageAcquisitionConfiguration,
    OpenRouterImageBillablePolicy,
)
from backend.src.production.image_acquisition.diagnostics import (
    ImageDiagnosticSubtype,
)
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionManifestConflictException,
)
from backend.src.production.image_acquisition.handler import ImageAcquisitionHandler
from backend.src.production.image_acquisition.manifest_writer import (
    InMemoryImageAcquisitionManifestWriter,
)
from backend.src.production.image_acquisition.models import (
    ImageAcquisitionEntryStatus,
    OpenRouterImageRequestStatus,
)
from backend.src.production.image_acquisition.ports import (
    GeneratedImagePayload,
    ImageAcquisitionProviderResponse,
)
from backend.src.production.image_acquisition.prompt_builder import (
    ImageGenerationPromptBuilder,
)
from backend.src.production.image_acquisition.providers.openrouter_provider import (
    OpenRouterImageAcquisitionProvider,
)
from backend.tests.unit.production.image_acquisition.test_handler_manifest_recovery import (
    FakeReader,
    store,
)


def _png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (64, 64), "navy").save(stream, "PNG")
    return stream.getvalue()


def _real_contract_jpeg_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (768, 1376), "firebrick").save(stream, "JPEG", quality=95)
    return stream.getvalue()


def _single_asset_source(source_visual_plan):
    plan = source_visual_plan.visual_asset_plan.model_copy(
        update={"assets": (source_visual_plan.visual_asset_plan.assets[0],)}
    )
    return source_visual_plan.model_copy(update={"visual_asset_plan": plan})


def _real_contract_source(source_visual_plan):
    asset = source_visual_plan.visual_asset_plan.assets[0].model_copy(
        update={"width": 576, "height": 1024, "aspect_ratio": "9:16"}
    )
    plan = source_visual_plan.visual_asset_plan.model_copy(
        update={"aspect_ratio": "9:16", "assets": (asset,)}
    )
    return source_visual_plan.model_copy(update={"visual_asset_plan": plan})


def _handler(*, source, provider, manifest_writer, binary_reader, binary_writer=None):
    return ImageAcquisitionHandler(
        plan_reader=FakeReader(source),
        provider=provider,
        manifest_writer=manifest_writer,
        binary_reader=binary_reader,
        binary_writer=binary_writer or binary_reader,
        configuration=ImageAcquisitionConfiguration(),
        provider_name="openrouter",
        requested_model="google/gemini-3.1-flash-lite-image",
        prompt_builder=ImageGenerationPromptBuilder(),
        clock=lambda: datetime.now(UTC),
        billable_policy=OpenRouterImageBillablePolicy(
            allow_billable_requests=True,
            estimated_cost_usd=Decimal("0.001"),
            maximum_authorized_cost_usd=Decimal("0.01"),
            maximum_requests_per_job=1,
        ),
    )


class _StaticProvider:
    async def generate_image(self, request):
        return ImageAcquisitionProviderResponse(
            images=(
                GeneratedImagePayload(
                    content=_png_bytes(),
                    mime_type="image/png",
                    index=0,
                    provider_metadata={"width": 64, "height": 64},
                ),
            ),
            provider="openrouter",
            requested_model="google/gemini-3.1-flash-lite-image",
            reported_model="reported/model",
            request_id="safe-generation-id",
            input_tokens=4,
            output_tokens=2,
            total_tokens=6,
            cost_usd=Decimal("0.0004"),
            http_status=200,
            latency_ms=12.5,
            finish_reason="stop",
        )

    async def close(self) -> None:
        return None


class _FailingBinaryWriter:
    def __init__(self, error) -> None:
        self.error = error

    async def write(self, *, request, content):
        raise self.error


class _FailingManifestWriter(InMemoryImageAcquisitionManifestWriter):
    async def checkpoint(self, *, context, previous, current) -> None:
        raise ImageAcquisitionManifestConflictException(
            "image acquisition checkpoint changed concurrently"
        )


@pytest.mark.asyncio
async def test_2xx_validation_failure_preserves_metadata_and_blocks_resubmission(
    tmp_path,
    source_visual_plan,
    image_command_context,
) -> None:
    calls = 0

    def respond(_):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "id": "safe-request-id",
                "model": "reported/model",
                "data": [{"b64_json": "%%%", "media_type": "image/png"}],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                    "cost": "0.0005",
                },
                "finish_reason": "stop",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    provider = OpenRouterImageAcquisitionProvider(
        api_key="fake-test-key-never-persisted",
        model="google/gemini-3.1-flash-lite-image",
        prompt_builder=ImageGenerationPromptBuilder(),
        client=client,
        max_transport_attempts=1,
    )
    writer = InMemoryImageAcquisitionManifestWriter()
    command, context = image_command_context
    acquired = _handler(
        source=_single_asset_source(source_visual_plan),
        provider=provider,
        manifest_writer=writer,
        binary_reader=store(tmp_path),
    )

    first = await acquired.execute(command, context)
    assert first.result.outcome is StageOutcome.FAILED_PERMANENT
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    entry = manifest.entries[0]
    assert entry.status is ImageAcquisitionEntryStatus.FAILED_PERMANENT
    assert entry.request_status is OpenRouterImageRequestStatus.FAILED
    assert entry.fresh_submission_permitted is False
    assert entry.diagnostic_subtype is ImageDiagnosticSubtype.INVALID_BASE64
    assert entry.http_status == 200
    assert entry.provider_request_id == "safe-request-id"
    assert entry.requested_model == "google/gemini-3.1-flash-lite-image"
    assert entry.reported_model == "reported/model"
    assert (entry.input_tokens, entry.output_tokens, entry.total_tokens) == (7, 3, 10)
    assert entry.cost_usd == Decimal("0.0005")
    assert entry.finish_reason == "stop"
    assert entry.diagnostic_metadata is not None
    assert entry.diagnostic_metadata.declared_media_type == "image/png"
    serialized = json.dumps(manifest.model_dump(mode="json"))
    assert "b64_json" not in serialized
    assert "fake-test-key-never-persisted" not in serialized
    assert "Authorization" not in serialized

    second = await acquired.execute(command, context)
    assert second.result.outcome is StageOutcome.FAILED_PERMANENT
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_requested_png_accepts_real_valid_jpeg_response_contract(
    tmp_path,
    source_visual_plan,
    image_command_context,
) -> None:
    content = _real_contract_jpeg_bytes()
    calls = 0

    def respond(http_request):
        nonlocal calls
        calls += 1
        sent = json.loads(http_request.content)
        assert sent["output_format"] == "png"
        assert sent["resolution"] == "1K"
        assert sent["aspect_ratio"] == "9:16"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "b64_json": base64.b64encode(content).decode(),
                        "media_type": "image/jpeg",
                    }
                ],
                "usage": {
                    "prompt_tokens": 353,
                    "completion_tokens": 1120,
                    "total_tokens": 1473,
                    "cost": "0.03368825",
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    provider = OpenRouterImageAcquisitionProvider(
        api_key="fake-test-key-never-persisted",
        model="google/gemini-3.1-flash-lite-image",
        prompt_builder=ImageGenerationPromptBuilder(),
        client=client,
        max_transport_attempts=1,
    )
    writer = InMemoryImageAcquisitionManifestWriter()
    command, context = image_command_context
    output = await _handler(
        source=_real_contract_source(source_visual_plan),
        provider=provider,
        manifest_writer=writer,
        binary_reader=store(tmp_path),
    ).execute(command, context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert calls == 1
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    entry = manifest.entries[0]
    assert entry.status is ImageAcquisitionEntryStatus.STORED
    assert entry.request_status is OpenRouterImageRequestStatus.COMPLETED
    assert entry.fresh_submission_permitted is False
    assert entry.mime_type == "image/jpeg"
    assert entry.extension == "jpg"
    assert entry.width == 768
    assert entry.height == 1376
    assert entry.size_bytes == len(content)
    assert entry.cost_usd == Decimal("0.03368825")
    assert entry.diagnostic_metadata is not None
    assert entry.diagnostic_metadata.declared_media_type == "image/jpeg"
    assert entry.diagnostic_metadata.detected_media_type == "image/jpeg"
    assert entry.diagnostic_metadata.decoded_format == "JPEG"
    assert entry.diagnostic_metadata.expected_width == 576
    assert entry.diagnostic_metadata.expected_height == 1024
    assert entry.diagnostic_metadata.decoded_width == 768
    assert entry.diagnostic_metadata.decoded_height == 1376
    assert entry.diagnostic_metadata.decoded_size_bytes == len(content)
    assert entry.diagnostic_metadata.requested_output_format == "png"
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "subtype"),
    [
        (
            BinaryAssetMimeError("binary MIME validation failed"),
            ImageDiagnosticSubtype.BINARY_ASSET_VALIDATION,
        ),
        (BinaryAssetIOError("binary write failed"), ImageDiagnosticSubtype.BINARY_ASSET_WRITE),
    ],
)
async def test_binary_failure_keeps_completed_response_metadata(
    tmp_path,
    source_visual_plan,
    image_command_context,
    error,
    subtype,
) -> None:
    writer = InMemoryImageAcquisitionManifestWriter()
    command, context = image_command_context
    output = await _handler(
        source=_single_asset_source(source_visual_plan),
        provider=_StaticProvider(),
        manifest_writer=writer,
        binary_reader=store(tmp_path),
        binary_writer=_FailingBinaryWriter(error),
    ).execute(command, context)

    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    entry = manifest.entries[0]
    assert entry.request_status is OpenRouterImageRequestStatus.FAILED
    assert entry.fresh_submission_permitted is False
    assert entry.diagnostic_subtype is subtype
    assert entry.http_status == 200
    assert entry.provider_request_id == "safe-generation-id"
    assert entry.reported_model == "reported/model"
    assert entry.total_tokens == 6
    assert entry.cost_usd == Decimal("0.0004")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport_error",
    [httpx.ReadTimeout("timeout"), httpx.ConnectError("connection")],
)
async def test_ambiguous_transport_is_uncertain_and_never_retried(
    tmp_path,
    source_visual_plan,
    image_command_context,
    transport_error,
) -> None:
    calls = 0

    def respond(_):
        nonlocal calls
        calls += 1
        raise transport_error

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    provider = OpenRouterImageAcquisitionProvider(
        api_key="fake-test-key",
        model="google/gemini-3.1-flash-lite-image",
        prompt_builder=ImageGenerationPromptBuilder(),
        client=client,
        max_transport_attempts=1,
    )
    writer = InMemoryImageAcquisitionManifestWriter()
    command, context = image_command_context
    acquired = _handler(
        source=_single_asset_source(source_visual_plan),
        provider=provider,
        manifest_writer=writer,
        binary_reader=store(tmp_path),
    )

    output = await acquired.execute(command, context)
    assert output.result.outcome is StageOutcome.NEEDS_USER_ACTION
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    entry = manifest.entries[0]
    assert entry.status is ImageAcquisitionEntryStatus.UNCERTAIN
    assert entry.request_status is OpenRouterImageRequestStatus.UNCERTAIN
    assert entry.fresh_submission_permitted is False
    assert entry.diagnostic_subtype is ImageDiagnosticSubtype.UNCERTAIN_TRANSPORT
    restarted = await acquired.execute(command, context)
    assert restarted.result.outcome is StageOutcome.NEEDS_USER_ACTION
    assert calls == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_cancellation_is_checkpointed_as_uncertain(
    tmp_path,
    source_visual_plan,
    image_command_context,
) -> None:
    class _CancelledProvider:
        async def generate_image(self, request):
            raise asyncio.CancelledError

        async def close(self) -> None:
            return None

    writer = InMemoryImageAcquisitionManifestWriter()
    command, context = image_command_context
    acquired = _handler(
        source=_single_asset_source(source_visual_plan),
        provider=_CancelledProvider(),
        manifest_writer=writer,
        binary_reader=store(tmp_path),
    )
    with pytest.raises(asyncio.CancelledError):
        await acquired.execute(command, context)
    manifest = await writer.read_existing(context=context)
    assert manifest is not None
    entry = manifest.entries[0]
    assert entry.request_status is OpenRouterImageRequestStatus.UNCERTAIN
    assert entry.fresh_submission_permitted is False
    assert entry.diagnostic_subtype is ImageDiagnosticSubtype.UNCERTAIN_TRANSPORT


@pytest.mark.asyncio
async def test_manifest_write_failure_has_stage_diagnostic(
    tmp_path,
    source_visual_plan,
    image_command_context,
) -> None:
    command, context = image_command_context
    output = await _handler(
        source=_single_asset_source(source_visual_plan),
        provider=_StaticProvider(),
        manifest_writer=_FailingManifestWriter(),
        binary_reader=store(tmp_path),
    ).execute(command, context)

    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert (
        output.result.metadata["diagnostic_subtype"] == ImageDiagnosticSubtype.MANIFEST_WRITE.value
    )
