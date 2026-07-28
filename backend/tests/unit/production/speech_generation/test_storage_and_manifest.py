import os
from pathlib import Path

import pytest

from backend.src.production.speech_generation.exceptions import (
    SpeechAudioConflictError,
    SpeechAudioIntegrityError,
    SpeechAudioPathError,
    SpeechManifestConflictError,
    SpeechManifestCorruptError,
)
from backend.src.production.speech_generation.manifest_writer import (
    LocalSpeechManifestWriter,
    speech_manifest_relative_path,
)
from backend.src.production.speech_generation.models import (
    SpeechGenerationManifest,
    SpeechGenerationManifestStatus,
    SpeechSegmentManifestEntry,
    SpeechSegmentStatus,
    summarize_speech_entries,
)
from backend.src.production.speech_generation.providers import (
    SimulatedSpeechGenerationProvider,
)
from backend.tests.unit.production.speech_generation.conftest import (
    JOB_ID,
    NOW,
    audio_store,
    command_context,
    source_script,
    speech_configuration,
    speech_requests,
)


async def test_audio_store_is_write_once_idempotent_and_verified(tmp_path: Path) -> None:
    configuration = speech_configuration()
    provider_request, write_request = speech_requests(
        source_script(),
        configuration,
    )
    content = (await SimulatedSpeechGenerationProvider().generate(provider_request)).content
    store = audio_store(tmp_path, configuration)

    first = await store.write(request=write_request, content=content)
    second = await store.write(request=write_request, content=content)
    read = await store.resolve(job_id=JOB_ID, segment_id=write_request.segment.segment_id)

    assert first == second == read.asset
    assert read.content == content
    assert first.storage_path.startswith(f"production/{JOB_ID}/assets/speech/")


async def test_audio_store_rejects_incompatible_duplicate(tmp_path: Path) -> None:
    configuration = speech_configuration()
    provider_request, write_request = speech_requests(
        source_script(),
        configuration,
    )
    provider = SimulatedSpeechGenerationProvider()
    original = (await provider.generate(provider_request)).content
    store = audio_store(tmp_path, configuration)
    await store.write(request=write_request, content=original)
    changed = bytearray(original)
    changed[-2:] = b"\x01\x00"
    with pytest.raises(SpeechAudioConflictError):
        await store.write(request=write_request, content=bytes(changed))


async def test_audio_store_detects_corruption_and_hard_links(tmp_path: Path) -> None:
    configuration = speech_configuration()
    provider_request, write_request = speech_requests(source_script(), configuration)
    content = (await SimulatedSpeechGenerationProvider().generate(provider_request)).content
    store = audio_store(tmp_path, configuration)
    asset = await store.write(request=write_request, content=content)
    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    target.write_bytes(content[:-2] + b"\x00\x00")
    with pytest.raises(SpeechAudioIntegrityError):
        await store.read(asset=asset)

    target.write_bytes(content)
    hard_link = target.with_name("linked.wav")
    os.link(target, hard_link)
    try:
        with pytest.raises(SpeechAudioPathError):
            await store.read(asset=asset)
    finally:
        hard_link.unlink()


async def test_audio_store_recovers_audio_written_before_sidecar(tmp_path: Path) -> None:
    configuration = speech_configuration()
    provider_request, write_request = speech_requests(source_script(), configuration)
    content = (await SimulatedSpeechGenerationProvider().generate(provider_request)).content
    store = audio_store(tmp_path, configuration)
    asset = await store.write(request=write_request, content=content)
    sidecar = tmp_path.joinpath(*f"{asset.storage_path}.asset.json".split("/"))
    sidecar.unlink()

    recovered = await store.recover(request=write_request)
    assert recovered is not None
    assert recovered.sha256 == asset.sha256
    assert sidecar.exists()


def _initial_manifest() -> SpeechGenerationManifest:
    source = source_script()
    configuration = speech_configuration()
    segments = (speech_requests(source, configuration)[0].segment,)
    entries = tuple(
        SpeechSegmentManifestEntry(
            segment_id=segment.segment_id,
            sequence_index=segment.sequence_index,
            source_scene_id=segment.scene_id,
            narration_text=segment.narration_text,
            normalized_text_hash=segment.normalized_text_hash,
            target_duration_ms=segment.target_duration_ms,
            timing_provenance=segment.timing_provenance,
            status=SpeechSegmentStatus.PENDING,
        )
        for segment in segments
    )
    return SpeechGenerationManifest(
        job_id=JOB_ID,
        attempt_number=1,
        source_script_schema_version=source.schema_version,
        source_script_artifact_id=source.artifact_id,
        source_script_sha256=source.sha256,
        provider="simulated",
        requested_voice=configuration.voice,
        requested_language=source.script.language,
        requested_speaking_rate=configuration.words_per_minute,
        configuration_fingerprint=configuration.fingerprint(),
        entries=entries,
        summary=summarize_speech_entries(entries),
        status=SpeechGenerationManifestStatus.IN_PROGRESS,
        created_at=NOW,
        updated_at=NOW,
    )


async def test_manifest_writer_round_trip_cas_and_corruption(tmp_path: Path) -> None:
    _, context = command_context()
    writer = LocalSpeechManifestWriter(tmp_path, max_manifest_bytes=100_000)
    manifest = _initial_manifest()
    await writer.create(context=context, manifest=manifest)
    assert await writer.read_existing(context=context) == manifest

    current = manifest.model_copy(update={"updated_at": NOW})
    stale = manifest.model_copy(update={"metadata": {"stale": True}})
    await writer.checkpoint(context=context, previous=manifest, current=current)
    with pytest.raises(SpeechManifestConflictError):
        await writer.checkpoint(context=context, previous=stale, current=current)

    path = tmp_path.joinpath(*speech_manifest_relative_path(context).split("/"))
    path.write_bytes(b"{invalid")
    with pytest.raises(SpeechManifestCorruptError):
        await writer.read_existing(context=context)


def test_manifest_path_rejects_wrong_stage_workspace() -> None:
    _, context = command_context()
    bad = context.model_copy(
        update={"workspace_relative_path": f"production/{JOB_ID}/planning/attempt-1"}
    )
    with pytest.raises(SpeechManifestConflictError):
        speech_manifest_relative_path(bad)
