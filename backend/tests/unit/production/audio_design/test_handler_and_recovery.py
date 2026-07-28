from __future__ import annotations

import asyncio

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.audio_design.exceptions import AudioDesignProviderError
from backend.src.production.audio_design.handler import AudioDesignHandler
from backend.src.production.audio_design.manifest_store import (
    audio_design_manifest_relative_path,
)
from backend.src.production.audio_design.models import (
    AudioDesignAssetStatus,
    AudioDesignManifestStatus,
    summarize_audio_design_entries,
)
from backend.src.production.audio_design.providers import (
    SimulatedMusicGenerationProvider,
    SimulatedSoundEffectGenerationProvider,
)
from backend.src.production.audio_design.serialization import (
    deserialize_audio_design_manifest,
    serialize_audio_design_manifest,
)
from backend.src.production.domain.enums import ArtifactType

from .conftest import NOW, build_runtime, make_script


class CountingMusicProvider(SimulatedMusicGenerationProvider):
    def __init__(self, configuration) -> None:
        super().__init__(configuration)
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return await super().generate(request)


class CountingSoundEffectProvider(SimulatedSoundEffectGenerationProvider):
    def __init__(self, configuration) -> None:
        super().__init__(configuration)
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return await super().generate(request)


class FailingSoundEffectProvider(CountingSoundEffectProvider):
    async def generate(self, request):
        self.calls += 1
        raise AudioDesignProviderError("safe simulated fixture failure")


def _replace_providers(runtime, *, music=None, sound=None) -> None:
    runtime.music_provider = music or runtime.music_provider
    runtime.sound_effect_provider = sound or runtime.sound_effect_provider
    runtime.handler = AudioDesignHandler(
        script_reader=runtime.reader,
        music_provider=runtime.music_provider,
        sound_effect_provider=runtime.sound_effect_provider,
        music_store=runtime.music_store,
        sound_effect_store=runtime.sound_effect_store,
        manifest_store=runtime.manifest_store,
        configuration=runtime.configuration,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("script", "music_count", "sfx_count"),
    [
        (make_script(), 0, 0),
        (make_script(music={"enabled": True}), 1, 0),
        (
            make_script(
                scene_effects=(({"cue_type": "alert"},), ()),
            ),
            0,
            1,
        ),
        (
            make_script(
                music={"enabled": True},
                scene_effects=(({"cue_type": "transition"},), ()),
            ),
            1,
            1,
        ),
    ],
)
async def test_handler_supports_zero_music_sfx_only_and_combined(
    tmp_path,
    script,
    music_count,
    sfx_count,
) -> None:
    runtime = build_runtime(tmp_path, script=script)

    output = await runtime.handler.execute(runtime.command, runtime.context)
    manifest = await runtime.manifest_store.read_existing(context=runtime.context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert manifest is not None
    assert manifest.status is AudioDesignManifestStatus.COMPLETE
    assert manifest.summary.music_assets == music_count
    assert manifest.summary.sound_effect_assets == sfx_count
    assert len(output.artifacts) == music_count + sfx_count + 1
    assert output.artifacts[-1].artifact_type is ArtifactType.PRODUCTION_AUDIO_DESIGN_MANIFEST


@pytest.mark.asyncio
async def test_repeated_invocation_is_idempotent_and_preserves_bytes(
    tmp_path,
    explicit_audio_script,
) -> None:
    runtime = build_runtime(tmp_path, script=explicit_audio_script)
    music = CountingMusicProvider(runtime.configuration)
    sound = CountingSoundEffectProvider(runtime.configuration)
    _replace_providers(runtime, music=music, sound=sound)

    first = await runtime.handler.execute(runtime.command, runtime.context)
    files_before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*.wav")
    }
    second = await runtime.handler.execute(runtime.command, runtime.context)
    files_after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes() for path in tmp_path.rglob("*.wav")
    }

    assert first.result.outcome is second.result.outcome is StageOutcome.SUCCEEDED
    assert music.calls == 1
    assert sound.calls == 2
    assert files_before == files_after


@pytest.mark.asyncio
async def test_concurrent_duplicate_execution_submits_each_asset_at_most_once(
    tmp_path,
) -> None:
    runtime = build_runtime(tmp_path, script=make_script(music={"enabled": True}))
    music = CountingMusicProvider(runtime.configuration)
    _replace_providers(runtime, music=music)

    first, duplicate = await asyncio.gather(
        runtime.handler.execute(runtime.command, runtime.context),
        runtime.handler.execute(runtime.command, runtime.context),
    )

    assert {first.result.outcome, duplicate.result.outcome} == {
        StageOutcome.SUCCEEDED,
        StageOutcome.FAILED_TRANSIENT,
    }
    assert music.calls == 1
    replay = await runtime.handler.execute(runtime.command, runtime.context)
    assert replay.result.outcome is StageOutcome.SUCCEEDED
    assert music.calls == 1


@pytest.mark.asyncio
async def test_partial_failure_checkpoints_and_retry_generates_only_failed_asset(
    tmp_path,
) -> None:
    script = make_script(
        music={"enabled": True},
        scene_effects=(({"cue_type": "alert"},), ()),
    )
    runtime = build_runtime(tmp_path, script=script)
    music = CountingMusicProvider(runtime.configuration)
    failing = FailingSoundEffectProvider(runtime.configuration)
    _replace_providers(runtime, music=music, sound=failing)

    failed = await runtime.handler.execute(runtime.command, runtime.context)
    manifest = await runtime.manifest_store.read_existing(context=runtime.context)

    assert failed.result.outcome is StageOutcome.FAILED_PERMANENT
    assert manifest is not None
    assert manifest.status is AudioDesignManifestStatus.FAILED
    assert manifest.entries[0].status is AudioDesignAssetStatus.STORED
    assert manifest.entries[1].status is AudioDesignAssetStatus.FAILED

    recovered_sound = CountingSoundEffectProvider(runtime.configuration)
    _replace_providers(runtime, music=music, sound=recovered_sound)
    recovered = await runtime.handler.execute(runtime.command, runtime.context)

    assert recovered.result.outcome is StageOutcome.SUCCEEDED
    assert music.calls == 1
    assert recovered_sound.calls == 1


@pytest.mark.asyncio
async def test_missing_stored_file_is_regenerated_without_touching_valid_assets(
    tmp_path,
) -> None:
    script = make_script(
        music={"enabled": True},
        scene_effects=(({"cue_type": "alert"},), ()),
    )
    runtime = build_runtime(tmp_path, script=script)
    music = CountingMusicProvider(runtime.configuration)
    sound = CountingSoundEffectProvider(runtime.configuration)
    _replace_providers(runtime, music=music, sound=sound)
    await runtime.handler.execute(runtime.command, runtime.context)
    manifest = await runtime.manifest_store.read_existing(context=runtime.context)
    assert manifest is not None
    effect = manifest.entries[1]
    assert effect.storage_path is not None
    target = tmp_path.joinpath(*effect.storage_path.split("/"))
    target.unlink()
    target.with_name(f"{target.name}.asset.json").unlink()
    music_bytes = tuple((tmp_path / "production").rglob("assets/music/*.wav"))[0].read_bytes()

    recovered = await runtime.handler.execute(runtime.command, runtime.context)

    assert recovered.result.outcome is StageOutcome.SUCCEEDED
    assert music.calls == 1
    assert sound.calls == 2
    assert (
        tuple((tmp_path / "production").rglob("assets/music/*.wav"))[0].read_bytes() == music_bytes
    )


@pytest.mark.asyncio
async def test_corrupt_stored_file_fails_without_overwrite(tmp_path) -> None:
    runtime = build_runtime(tmp_path, script=make_script(music={"enabled": True}))
    await runtime.handler.execute(runtime.command, runtime.context)
    manifest = await runtime.manifest_store.read_existing(context=runtime.context)
    assert manifest is not None
    entry = manifest.entries[0]
    assert entry.storage_path is not None
    target = tmp_path.joinpath(*entry.storage_path.split("/"))
    content = target.read_bytes()
    corrupt = content[:-2] + b"\x00\x00"
    target.write_bytes(corrupt)

    output = await runtime.handler.execute(runtime.command, runtime.context)

    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert target.read_bytes() == corrupt


@pytest.mark.asyncio
async def test_file_written_before_manifest_checkpoint_is_adopted(tmp_path) -> None:
    runtime = build_runtime(tmp_path, script=make_script(music={"enabled": True}))
    music = CountingMusicProvider(runtime.configuration)
    _replace_providers(runtime, music=music)
    await runtime.handler.execute(runtime.command, runtime.context)
    path = audio_design_manifest_relative_path(runtime.context)
    complete = deserialize_audio_design_manifest(runtime.manifest_store.contents[path])
    pending_entries = tuple(
        entry.model_copy(
            update={
                "status": AudioDesignAssetStatus.PENDING,
                "generation_attempt_count": 0,
                "stored_at": None,
                "asset_id": None,
                "artifact_id": None,
                "storage_path": None,
                "sha256": None,
                "size_bytes": None,
                "metadata": {},
            }
        )
        for entry in complete.entries
    )
    prepared = complete.model_copy(
        update={
            "entries": pending_entries,
            "summary": summarize_audio_design_entries(pending_entries),
            "status": AudioDesignManifestStatus.PREPARED,
            "updated_at": complete.created_at,
        }
    )
    runtime.manifest_store.contents[path] = serialize_audio_design_manifest(prepared)

    recovered = await runtime.handler.execute(runtime.command, runtime.context)

    assert recovered.result.outcome is StageOutcome.SUCCEEDED
    assert music.calls == 1
    final = await runtime.manifest_store.read_existing(context=runtime.context)
    assert final is not None
    assert final.entries[0].metadata["recovered"] is True


@pytest.mark.asyncio
async def test_changed_source_script_fingerprint_fails_closed(tmp_path) -> None:
    runtime = build_runtime(tmp_path, script=make_script(music={"enabled": True}))
    await runtime.handler.execute(runtime.command, runtime.context)
    runtime.reader.source = runtime.reader.source.model_copy(update={"sha256": "b" * 64})

    output = await runtime.handler.execute(runtime.command, runtime.context)

    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert output.result.error_code == "audio_design_invalid"


@pytest.mark.asyncio
async def test_manifest_checkpoint_precedes_provider_call(tmp_path) -> None:
    runtime = build_runtime(tmp_path, script=make_script(music={"enabled": True}))

    class ObservingProvider(CountingMusicProvider):
        async def generate(self, request):
            manifest = await runtime.manifest_store.read_existing(context=runtime.context)
            assert manifest is not None
            assert manifest.entries[0].status is AudioDesignAssetStatus.GENERATING
            return await super().generate(request)

    observer = ObservingProvider(runtime.configuration)
    _replace_providers(runtime, music=observer)

    output = await runtime.handler.execute(runtime.command, runtime.context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert observer.calls == 1
