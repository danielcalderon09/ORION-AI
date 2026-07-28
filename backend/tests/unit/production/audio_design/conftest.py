"""Shared deterministic fixtures for durable simulated audio design."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.audio_design.asset_store import (
    FilesystemAudioDesignAssetStore,
)
from backend.src.production.audio_design.configuration import (
    AudioDesignConfiguration,
)
from backend.src.production.audio_design.handler import AudioDesignHandler
from backend.src.production.audio_design.manifest_store import (
    InMemoryAudioDesignManifestStore,
)
from backend.src.production.audio_design.models import AudioAssetKind
from backend.src.production.audio_design.ports import ReadAudioDesignSourceScript
from backend.src.production.audio_design.providers import (
    SimulatedMusicGenerationProvider,
    SimulatedSoundEffectGenerationProvider,
)
from backend.src.production.audio_design.wav import AudioDesignWavValidator
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.runtime.context import StageContext
from backend.src.production.scripting.models import (
    ProductionScript,
    ProductionScriptScene,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
JOB_ID = UUID("10000000-0000-4000-8000-000000000801")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000801")
SCRIPT_ARTIFACT_ID = UUID("30000000-0000-4000-8000-000000000801")
SCRIPT_SHA256 = "a" * 64


class StaticAudioDesignSourceReader:
    def __init__(self, source: ReadAudioDesignSourceScript) -> None:
        self.source = source

    async def read_for_audio_design(
        self,
        *,
        context: StageContext,
    ) -> ReadAudioDesignSourceScript:
        assert context.job_id == JOB_ID
        return self.source


def make_script(
    *,
    music: dict[str, Any] | None = None,
    scene_effects: tuple[tuple[dict[str, Any], ...], ...] = ((), ()),
) -> ProductionScript:
    script_metadata: dict[str, Any] = {}
    if music is not None:
        script_metadata = {"audio_design": {"music": music}}
    scenes = []
    for index in (1, 2):
        effects = scene_effects[index - 1]
        scene_metadata: dict[str, Any] = {}
        if effects:
            scene_metadata = {"audio_design": {"sound_effects": [dict(item) for item in effects]}}
        scenes.append(
            ProductionScriptScene(
                scene_number=index,
                source_scene_number=index,
                heading=f"Scene {index}",
                narration=f"Approved narration {index}.",
                estimated_duration_seconds=1,
                delivery_style="neutral",
                visual_intent=f"Visual {index}",
                metadata=scene_metadata,
            )
        )
    return ProductionScript(
        source_plan_schema_version="1.0.0",
        title="Offline audio design",
        language="es-CO",
        target_duration_seconds=2,
        tone="neutral",
        opening_hook="Inicio seguro.",
        scenes=tuple(scenes),
        metadata=script_metadata,
    )


def make_source(script: ProductionScript) -> ReadAudioDesignSourceScript:
    return ReadAudioDesignSourceScript(
        script=script,
        artifact_id=SCRIPT_ARTIFACT_ID,
        relative_path=(f"production/{JOB_ID}/scripting/attempt-1/production-script.json"),
        sha256=SCRIPT_SHA256,
        size_bytes=1_024,
        schema_version="1.0.0",
        created_at=NOW,
    )


def make_command_context() -> tuple[StageCommand, StageContext]:
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        stage=ProductionStage.PREPARING_MUSIC,
        attempt_number=1,
        idempotency_key="audio-design:test",
        input_artifact_ids=(SCRIPT_ARTIFACT_ID,),
        configuration_snapshot={},
        created_at=NOW,
    )
    context = StageContext(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        stage=ProductionStage.PREPARING_MUSIC,
        attempt_number=1,
        input_artifact_ids=command.input_artifact_ids,
        workspace_relative_path=(f"production/{JOB_ID}/preparing_music/attempt-1"),
        correlation_id=JOB_ID,
    )
    return command, context


@dataclass
class AudioDesignRuntime:
    handler: AudioDesignHandler
    configuration: AudioDesignConfiguration
    reader: StaticAudioDesignSourceReader
    music_provider: SimulatedMusicGenerationProvider
    sound_effect_provider: SimulatedSoundEffectGenerationProvider
    music_store: FilesystemAudioDesignAssetStore
    sound_effect_store: FilesystemAudioDesignAssetStore
    manifest_store: InMemoryAudioDesignManifestStore
    command: StageCommand
    context: StageContext


def build_runtime(
    root: Path,
    *,
    script: ProductionScript,
    configuration: AudioDesignConfiguration | None = None,
) -> AudioDesignRuntime:
    config = configuration or AudioDesignConfiguration(
        max_music_duration_ms=5_000,
        max_audio_bytes=300_000,
    )
    source_reader = StaticAudioDesignSourceReader(make_source(script))
    music_provider = SimulatedMusicGenerationProvider(config)
    sound_effect_provider = SimulatedSoundEffectGenerationProvider(config)
    validator = AudioDesignWavValidator(max_audio_bytes=config.max_audio_bytes)
    music_store = FilesystemAudioDesignAssetStore(
        workspace_root=root,
        kind=AudioAssetKind.MUSIC,
        validator=validator,
        max_audio_bytes=config.max_audio_bytes,
    )
    sound_effect_store = FilesystemAudioDesignAssetStore(
        workspace_root=root,
        kind=AudioAssetKind.SOUND_EFFECT,
        validator=validator,
        max_audio_bytes=config.max_audio_bytes,
    )
    manifest_store = InMemoryAudioDesignManifestStore()
    command, context = make_command_context()
    handler = AudioDesignHandler(
        script_reader=source_reader,
        music_provider=music_provider,
        sound_effect_provider=sound_effect_provider,
        music_store=music_store,
        sound_effect_store=sound_effect_store,
        manifest_store=manifest_store,
        configuration=config,
        clock=lambda: NOW,
    )
    return AudioDesignRuntime(
        handler=handler,
        configuration=config,
        reader=source_reader,
        music_provider=music_provider,
        sound_effect_provider=sound_effect_provider,
        music_store=music_store,
        sound_effect_store=sound_effect_store,
        manifest_store=manifest_store,
        command=command,
        context=context,
    )


@pytest.fixture
def explicit_audio_script() -> ProductionScript:
    return make_script(
        music={
            "enabled": True,
            "mood": "calm",
            "intensity": 35,
            "loopable": True,
            "duck_under_narration": True,
        },
        scene_effects=(
            (
                {
                    "cue_type": "transition",
                    "duration_ms": 300,
                    "intensity": 40,
                    "offset_ms": 100,
                },
            ),
            (
                {
                    "cue_type": "soft_click",
                    "intensity": 20,
                    "offset_ms": 200,
                    "shot_id": "scene-002-shot-001",
                },
            ),
        ),
    )
