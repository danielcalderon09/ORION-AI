"""OpenRouter TTS tests use only an injected fake transport."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.speech_generation.exceptions import (
    SpeechBillableAuthorizationError,
    SpeechProviderResponseError,
    SpeechProviderUncertainError,
)
from backend.src.production.speech_generation.handler import SpeechGenerationHandler
from backend.src.production.speech_generation.manifest_writer import (
    InMemorySpeechManifestWriter,
)
from backend.src.production.speech_generation.providers.openrouter_provider import (
    OpenRouterSpeechGenerationProvider,
)
from backend.src.production.speech_generation.remote_job_store import (
    InMemoryRemoteSpeechJobStore,
)
from backend.src.production.speech_generation.remote_models import RemoteSpeechJobStatus
from backend.tests.unit.production.speech_generation.conftest import (
    NOW,
    FakeSourceReader,
    audio_store,
    command_context,
    source_script,
    speech_configuration,
    speech_requests,
)


def _request():
    configuration = speech_configuration(
        provider="openrouter",
        voice="configured-spanish-voice",
        min_duration_ms=100,
    )
    return speech_requests(source_script(), configuration)[0]


def _provider(transport, *, store=None, maximum=1, max_bytes=200_000):
    client = httpx.AsyncClient(transport=transport)
    return OpenRouterSpeechGenerationProvider(
        api_key="fake-key-never-real",
        model="hexgrad/kokoro-82m",
        voice="configured-spanish-voice",
        estimated_cost_usd=Decimal("0.0001"),
        maximum_authorized_cost_usd=Decimal("0.001"),
        allow_billable_requests=True,
        remote_job_store=store or InMemoryRemoteSpeechJobStore(),
        maximum_requests_per_job=maximum,
        max_audio_bytes=max_bytes,
        client=client,
        owns_client=True,
    )


@pytest.mark.asyncio
async def test_valid_pcm_becomes_wav_and_retains_safe_generation_id() -> None:
    observed = {}
    store = InMemoryRemoteSpeechJobStore()

    def handle(request):
        observed["url"] = str(request.url)
        observed["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=b"\x01\x00" * 4_800,
            headers={
                "content-type": "application/octet-stream",
                "x-generation-id": "generation_test_1",
            },
        )

    provider = _provider(httpx.MockTransport(handle), store=store)
    result = await provider.generate(_request())
    records = await store.list_records()
    assert observed["url"] == "https://openrouter.ai/api/v1/audio/speech"
    assert observed["payload"] == {
        "model": "hexgrad/kokoro-82m",
        "input": "Hola, mundo.",
        "voice": "configured-spanish-voice",
        "response_format": "pcm",
    }
    assert result.content[:12] == b"RIFF" + result.content[4:8] + b"WAVE"
    assert result.audio.duration_ms == 200
    assert result.metadata["provider_generation_id"] == "generation_test_1"
    assert records[0].status is RemoteSpeechJobStatus.COMPLETED
    assert records[0].fresh_submission_permitted is False
    assert records[0].remote_generation_id == "generation_test_1"
    assert "fake-key-never-real" not in records[0].model_dump_json()
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "headers"),
    [
        (b"", {"content-type": "application/octet-stream"}),
        (b"odd", {"content-type": "application/octet-stream"}),
        (b"\x00\x00" * 4_800, {"content-type": "text/html"}),
    ],
    ids=("empty", "odd-pcm", "wrong-mime"),
)
async def test_invalid_audio_fails_durably(content, headers) -> None:
    store = InMemoryRemoteSpeechJobStore()
    provider = _provider(
        httpx.MockTransport(lambda _: httpx.Response(200, content=content, headers=headers)),
        store=store,
    )
    with pytest.raises(SpeechProviderResponseError):
        await provider.generate(_request())
    record = (await store.list_records())[0]
    assert record.status is RemoteSpeechJobStatus.FAILED
    assert record.fresh_submission_permitted is False
    assert "fake-key-never-real" not in record.model_dump_json()
    await provider.close()


@pytest.mark.asyncio
async def test_oversized_audio_is_rejected_without_persisting_body() -> None:
    store = InMemoryRemoteSpeechJobStore()
    provider = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=b"\x01\x00" * 600,
                headers={
                    "content-type": "application/octet-stream",
                    "x-generation-id": "oversized_generation_1",
                },
            )
        ),
        store=store,
        max_bytes=1_024,
    )
    with pytest.raises(SpeechProviderResponseError, match="safe limits"):
        await provider.generate(_request())
    record = (await store.list_records())[0]
    assert record.status is RemoteSpeechJobStatus.FAILED
    assert record.remote_generation_id == "oversized_generation_1"
    assert record.metadata["http_status"] == 200
    assert "0100" not in record.model_dump_json()
    await provider.close()


def test_missing_voice_and_unauthorized_job_budget_fail_closed() -> None:
    common = {
        "api_key": "fake-key-never-real",
        "model": "hexgrad/kokoro-82m",
        "estimated_cost_usd": Decimal("0.001"),
        "maximum_authorized_cost_usd": Decimal("0.001"),
        "allow_billable_requests": True,
        "remote_job_store": InMemoryRemoteSpeechJobStore(),
    }
    with pytest.raises(ValueError, match="voice"):
        OpenRouterSpeechGenerationProvider(voice="", **common)
    with pytest.raises(SpeechBillableAuthorizationError, match="job cost"):
        OpenRouterSpeechGenerationProvider(
            voice="configured-spanish-voice",
            maximum_requests_per_job=2,
            **common,
        )


@pytest.mark.asyncio
async def test_timeout_is_uncertain_and_never_submits_again() -> None:
    calls = 0
    store = InMemoryRemoteSpeechJobStore()

    def timeout(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("fake timeout", request=request)

    provider = _provider(httpx.MockTransport(timeout), store=store)
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request())
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request())
    assert calls == 1
    record = (await store.list_records())[0]
    assert record.status is RemoteSpeechJobStatus.UNCERTAIN
    assert record.safe_error_code == "speech_transport_timeout"
    assert record.submitted_at is None
    assert record.remote_generation_id is None
    assert record.transport_diagnostic is not None
    assert record.transport_diagnostic.timeout_seconds == Decimal("120")
    assert record.transport_diagnostic.exception_class == "ReadTimeout"
    assert record.transport_diagnostic.elapsed_seconds is not None
    assert record.transport_diagnostic.endpoint_family == "api_v1_audio_speech"
    assert "fake-key-never-real" not in record.model_dump_json()
    await provider.close()


@pytest.mark.asyncio
async def test_request_error_is_uncertain_but_distinguishable_from_timeout() -> None:
    store = InMemoryRemoteSpeechJobStore()

    def reset(request: httpx.Request) -> httpx.Response:
        raise httpx.NetworkError("offline reset", request=request)

    provider = _provider(httpx.MockTransport(reset), store=store)
    with pytest.raises(SpeechProviderUncertainError):
        await provider.generate(_request())
    record = (await store.list_records())[0]
    assert record.status is RemoteSpeechJobStatus.UNCERTAIN
    assert record.safe_error_code == "speech_transport_error"
    assert record.transport_diagnostic is not None
    assert record.transport_diagnostic.exception_class == "NetworkError"
    await provider.close()


@pytest.mark.asyncio
async def test_cancellation_is_checkpointed_uncertain() -> None:
    store = InMemoryRemoteSpeechJobStore()

    def cancel(_):
        raise asyncio.CancelledError

    provider = _provider(httpx.MockTransport(cancel), store=store)
    with pytest.raises(asyncio.CancelledError):
        await provider.generate(_request())
    assert (await store.list_records())[0].status is RemoteSpeechJobStatus.UNCERTAIN
    await provider.close()


@pytest.mark.asyncio
async def test_http_error_is_failed_and_raw_body_is_not_persisted() -> None:
    store = InMemoryRemoteSpeechJobStore()
    provider = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(400, content=b'secret provider body {"error":true}')
        ),
        store=store,
    )
    with pytest.raises(SpeechProviderResponseError):
        await provider.generate(_request())
    record = (await store.list_records())[0]
    serialized = record.model_dump_json()
    assert record.status is RemoteSpeechJobStatus.FAILED
    assert record.metadata["http_status"] == 400
    assert "secret provider body" not in serialized
    await provider.close()


@pytest.mark.asyncio
async def test_real_adapter_output_satisfies_existing_speech_artifact_contract(tmp_path) -> None:
    configuration = speech_configuration(
        provider="openrouter",
        voice="configured-spanish-voice",
        min_duration_ms=100,
    )
    store = InMemoryRemoteSpeechJobStore()
    provider = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=b"\x01\x00" * 4_800,
                headers={"content-type": "application/octet-stream"},
            )
        ),
        store=store,
        maximum=2,
    )
    handler = SpeechGenerationHandler(
        script_reader=FakeSourceReader(source_script()),
        provider=provider,
        audio_store=audio_store(tmp_path, configuration),
        manifest_writer=InMemorySpeechManifestWriter(),
        configuration=configuration,
        clock=lambda: NOW,
    )
    command, context = command_context()
    result = await handler.execute(command, context)
    assert result.result.outcome is StageOutcome.SUCCEEDED
    assert len(result.artifacts) == 3
    assert len(await store.list_records()) == 2
    await provider.close()
