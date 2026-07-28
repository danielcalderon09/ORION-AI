from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

from backend.src.production.audio_design.asset_store import (
    FilesystemAudioDesignAssetStore,
    audio_asset_relative_path,
)
from backend.src.production.audio_design.configuration import (
    AudioDesignConfiguration,
)
from backend.src.production.audio_design.duration import (
    duration_for_frame_count,
    frame_count_for_duration,
)
from backend.src.production.audio_design.exceptions import (
    AudioDesignManifestConflictError,
    AudioDesignManifestCorruptError,
    AudioDesignStoreConflictError,
    AudioDesignStoreIntegrityError,
    AudioDesignStorePathError,
)
from backend.src.production.audio_design.manifest_store import (
    LocalAudioDesignManifestStore,
    audio_design_manifest_relative_path,
)
from backend.src.production.audio_design.models import (
    AudioAssetExpectation,
    AudioAssetKind,
    AudioDesignManifest,
    AudioDesignManifestStatus,
    AudioDesignSummary,
    AudioFormatExpectation,
    MusicGenerationRequest,
    MusicMood,
)
from backend.src.production.audio_design.providers import (
    SimulatedMusicGenerationProvider,
)
from backend.src.production.audio_design.serialization import (
    deserialize_audio_design_manifest,
    serialize_audio_design_manifest,
)
from backend.src.production.audio_design.wav import AudioDesignWavValidator

from .conftest import (
    JOB_ID,
    NOW,
    SCRIPT_ARTIFACT_ID,
    SCRIPT_SHA256,
    make_command_context,
)


def _configuration() -> AudioDesignConfiguration:
    return AudioDesignConfiguration(
        max_music_duration_ms=5_000,
        max_audio_bytes=300_000,
    )


def _request(fingerprint: str = "a" * 64) -> MusicGenerationRequest:
    return MusicGenerationRequest(
        request_id=f"music-request-{fingerprint[:24]}",
        requirement_id="music-" + "b" * 24,
        mood=MusicMood.CALM,
        intensity=30,
        duration_ms=1_000,
        loopable=True,
        request_fingerprint=fingerprint,
    )


def _expectation(fingerprint: str = "a" * 64) -> AudioAssetExpectation:
    frames = frame_count_for_duration(1_000, 24_000)
    return AudioAssetExpectation(
        job_id=JOB_ID,
        kind=AudioAssetKind.MUSIC,
        requirement_id="music-" + "b" * 24,
        request_fingerprint=fingerprint,
        provider_id="orion-simulated-music",
        audio=AudioFormatExpectation(
            duration_ms=duration_for_frame_count(frames, 24_000),
            frame_count=frames,
        ),
    )


def _manifest() -> AudioDesignManifest:
    return AudioDesignManifest(
        job_id=JOB_ID,
        attempt_number=1,
        source_script_schema_version="1.0.0",
        source_script_artifact_id=SCRIPT_ARTIFACT_ID,
        production_script_fingerprint=SCRIPT_SHA256,
        audio_design_plan_fingerprint="c" * 64,
        configuration_fingerprint="d" * 64,
        music_provider_id="orion-simulated-music",
        sound_effect_provider_id="orion-simulated-sound-effects",
        summary=AudioDesignSummary(
            expected=0,
            stored=0,
            pending=0,
            failed=0,
            music_assets=0,
            sound_effect_assets=0,
            total_duration_ms=0,
        ),
        status=AudioDesignManifestStatus.PREPARED,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_store_is_atomic_write_once_and_idempotent(tmp_path) -> None:
    config = _configuration()
    store = FilesystemAudioDesignAssetStore(
        workspace_root=tmp_path,
        kind=AudioAssetKind.MUSIC,
        validator=AudioDesignWavValidator(max_audio_bytes=config.max_audio_bytes),
        max_audio_bytes=config.max_audio_bytes,
    )
    provider = SimulatedMusicGenerationProvider(config)
    expectation = _expectation()
    content = (await provider.generate(_request())).content

    first = await store.write(expectation=expectation, content=content)
    second = await store.write(expectation=expectation, content=content)
    recovered = await store.recover(expectation=expectation)

    assert first == second == recovered
    assert first.storage_path == (
        f"production/{JOB_ID}/assets/music/"
        f"{expectation.requirement_id}-{expectation.request_fingerprint[:16]}.wav"
    )
    assert not tuple(tmp_path.rglob("*.tmp"))
    assert not tuple(tmp_path.rglob("*.lock"))


@pytest.mark.asyncio
async def test_store_rejects_incompatible_duplicate_and_corruption(tmp_path) -> None:
    config = _configuration()
    store = FilesystemAudioDesignAssetStore(
        workspace_root=tmp_path,
        kind=AudioAssetKind.MUSIC,
        validator=AudioDesignWavValidator(max_audio_bytes=config.max_audio_bytes),
        max_audio_bytes=config.max_audio_bytes,
    )
    provider = SimulatedMusicGenerationProvider(config)
    expectation = _expectation()
    original = (await provider.generate(_request())).content
    different = (
        await provider.generate(
            _request("e" * 64).model_copy(update={"request_id": "music-request-" + "e" * 24})
        )
    ).content
    asset = await store.write(expectation=expectation, content=original)

    with pytest.raises(AudioDesignStoreConflictError):
        await store.write(expectation=expectation, content=different)

    target = tmp_path.joinpath(*asset.storage_path.split("/"))
    target.write_bytes(original[:-2] + b"\x00\x00")
    with pytest.raises(AudioDesignStoreIntegrityError):
        await store.resolve(expectation=expectation)


@pytest.mark.asyncio
async def test_store_rejects_symlink_drift(tmp_path) -> None:
    config = _configuration()
    store = FilesystemAudioDesignAssetStore(
        workspace_root=tmp_path,
        kind=AudioAssetKind.MUSIC,
        validator=AudioDesignWavValidator(max_audio_bytes=config.max_audio_bytes),
        max_audio_bytes=config.max_audio_bytes,
    )
    expectation = _expectation()
    content = (await SimulatedMusicGenerationProvider(config).generate(_request())).content
    target = tmp_path.joinpath(*audio_asset_relative_path(expectation).split("/"))
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(content)
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links unavailable")
    with pytest.raises(AudioDesignStorePathError):
        await store.recover(expectation=expectation)


@pytest.mark.asyncio
async def test_store_rejects_hardlink_drift(tmp_path) -> None:
    config = _configuration()
    store = FilesystemAudioDesignAssetStore(
        workspace_root=tmp_path,
        kind=AudioAssetKind.MUSIC,
        validator=AudioDesignWavValidator(max_audio_bytes=config.max_audio_bytes),
        max_audio_bytes=config.max_audio_bytes,
    )
    expectation = _expectation()
    content = (await SimulatedMusicGenerationProvider(config).generate(_request())).content
    target = tmp_path.joinpath(*audio_asset_relative_path(expectation).split("/"))
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(content)
    os.link(outside, target)
    with pytest.raises(AudioDesignStorePathError):
        await store.recover(expectation=expectation)


def test_expectation_contract_rejects_traversal_identity() -> None:
    with pytest.raises(ValueError):
        AudioAssetExpectation(
            job_id=JOB_ID,
            kind=AudioAssetKind.MUSIC,
            requirement_id="../escape",  # type: ignore[arg-type]
            request_fingerprint="a" * 64,
            provider_id="simulated",
            audio=_expectation().audio,
        )


def test_manifest_serialization_is_canonical_strict_and_byte_free() -> None:
    manifest = _manifest()
    content = serialize_audio_design_manifest(manifest)

    assert content.endswith(b"\n")
    assert (
        content
        == (
            json.dumps(
                manifest.model_dump(mode="json"),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()
    )
    assert deserialize_audio_design_manifest(content) == manifest
    assert b"RIFF" not in content
    assert b"api_key" not in content and b"http" not in content


@pytest.mark.parametrize(
    "content",
    [
        b'{"schema_version":"1.0.0","schema_version":"1.0.0"}',
        b'{"schema_version":NaN}',
        b'{"schema_version":Infinity}',
    ],
)
def test_manifest_loader_rejects_duplicate_or_non_finite_json(content: bytes) -> None:
    with pytest.raises((ValueError, AudioDesignManifestCorruptError)):
        deserialize_audio_design_manifest(content)


@pytest.mark.asyncio
async def test_local_manifest_store_enforces_cas_and_contractual_path(tmp_path) -> None:
    store = LocalAudioDesignManifestStore(tmp_path, max_manifest_bytes=100_000)
    _, context = make_command_context()
    prepared = _manifest()
    complete = prepared.model_copy(
        update={
            "status": AudioDesignManifestStatus.COMPLETE,
            "updated_at": datetime(2026, 7, 29, 12, 1, tzinfo=UTC),
        }
    )

    await store.create(context=context, manifest=prepared)
    await store.finalize(context=context, previous=prepared, current=complete)

    assert await store.read_existing(context=context) == complete
    assert audio_design_manifest_relative_path(context).endswith(
        "/preparing_music/attempt-1/audio-design-manifest.json"
    )
    with pytest.raises(AudioDesignManifestConflictError, match="CAS"):
        await store.checkpoint(
            context=context,
            previous=prepared,
            current=complete,
        )


def test_unsupported_manifest_schema_fails_explicitly() -> None:
    content = serialize_audio_design_manifest(_manifest()).replace(
        b'"schema_version":"1.0.0"',
        b'"schema_version":"9.0.0"',
    )
    with pytest.raises(ValueError, match="unsupported"):
        deserialize_audio_design_manifest(content)
