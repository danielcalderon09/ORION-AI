import json
from pathlib import Path

from backend.src.infrastructure.config.settings import Settings
from backend.src.production.application.orchestration.stage_registry import StageRegistry
from backend.src.production.application.results import StageOutcome
from backend.src.production.composition.container import build_production_container
from backend.src.production.domain.enums import ProductionStage
from backend.src.production.infrastructure.persistence.session import (
    sqlite_url_from_path,
)
from backend.src.production.speech_generation.handler import SpeechGenerationHandler
from backend.src.production.speech_generation.manifest_writer import (
    LocalSpeechManifestWriter,
)
from backend.src.production.speech_generation.providers import (
    SimulatedSpeechGenerationProvider,
)
from backend.src.production.speech_generation.reconciliation import (
    SpeechGenerationReconciler,
    SpeechReconciliationIssueKind,
)
from backend.tests.unit.production.speech_generation.conftest import (
    FakeSourceReader,
    audio_store,
    command_context,
    source_script,
    speech_configuration,
)


class RegisteredPaths:
    def __init__(self, paths=frozenset()) -> None:
        self.paths = frozenset(paths)

    def list_registered_paths(self):
        return self.paths


async def _durable_speech(root: Path):
    configuration = speech_configuration()
    reader = FakeSourceReader(source_script())
    store = audio_store(root, configuration)
    writer = LocalSpeechManifestWriter(
        root,
        max_manifest_bytes=configuration.max_manifest_bytes,
    )
    handler = SpeechGenerationHandler(
        script_reader=reader,
        provider=SimulatedSpeechGenerationProvider(),
        audio_store=store,
        manifest_writer=writer,
        configuration=configuration,
        clock=lambda: source_script().created_at,
    )
    command, context = command_context()
    output = await handler.execute(command, context)
    assert output.result.outcome is StageOutcome.SUCCEEDED
    return reader, store, output


async def test_reconciliation_healthy_and_read_only(tmp_path: Path) -> None:
    reader, store, output = await _durable_speech(tmp_path)
    registered = RegisteredPaths(frozenset(item.relative_path for item in output.artifacts))
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    report = await SpeechGenerationReconciler(
        workspace_root=tmp_path,
        audio_store=store,
        source_reader=reader,
        registered_reader=registered,
        max_manifest_bytes=100_000,
    ).reconcile()
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    assert report.issues == ()
    assert report.valid == 2
    assert before == after


async def test_reconciliation_detects_missing_and_orphan_audio(tmp_path: Path) -> None:
    reader, store, output = await _durable_speech(tmp_path)
    audio = next(item for item in output.artifacts if item.relative_path.endswith(".wav"))
    target = tmp_path.joinpath(*audio.relative_path.split("/"))
    target.unlink()
    orphan = target.with_name("speech-segment-" + "f" * 32 + ".wav")
    orphan.write_bytes(b"orphan")
    report = await SpeechGenerationReconciler(
        workspace_root=tmp_path,
        audio_store=store,
        source_reader=reader,
        registered_reader=RegisteredPaths(),
        max_manifest_bytes=100_000,
    ).reconcile()
    kinds = {issue.kind for issue in report.issues}
    assert SpeechReconciliationIssueKind.MISSING_AUDIO in kinds
    assert SpeechReconciliationIssueKind.ORPHAN_AUDIO in kinds


async def test_reconciliation_classifies_duplicate_segment_drift(
    tmp_path: Path,
) -> None:
    reader, store, output = await _durable_speech(tmp_path)
    manifest_artifact = next(
        item
        for item in output.artifacts
        if item.relative_path.endswith("speech-generation-manifest.json")
    )
    manifest_path = tmp_path.joinpath(*manifest_artifact.relative_path.split("/"))
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["entries"][1]["segment_id"] = payload["entries"][0]["segment_id"]
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    report = await SpeechGenerationReconciler(
        workspace_root=tmp_path,
        audio_store=store,
        source_reader=reader,
        registered_reader=RegisteredPaths(),
        max_manifest_bytes=100_000,
    ).reconcile()
    kinds = {issue.kind for issue in report.issues}
    assert SpeechReconciliationIssueKind.DUPLICATE_SEGMENT in kinds


async def test_reconciliation_classifies_audio_checksum_drift(
    tmp_path: Path,
) -> None:
    reader, store, output = await _durable_speech(tmp_path)
    audio = next(item for item in output.artifacts if item.relative_path.endswith(".wav"))
    target = tmp_path.joinpath(*audio.relative_path.split("/"))
    content = bytearray(target.read_bytes())
    content[-2:] = b"\x01\x00"
    target.write_bytes(content)
    report = await SpeechGenerationReconciler(
        workspace_root=tmp_path,
        audio_store=store,
        source_reader=reader,
        registered_reader=RegisteredPaths(),
        max_manifest_bytes=100_000,
    ).reconcile()
    assert SpeechReconciliationIssueKind.AUDIO_CHECKSUM_MISMATCH in {
        issue.kind for issue in report.issues
    }


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "ORION_HOME": tmp_path / "home",
        "MODELS_DIR": tmp_path / "models",
        "PROJECTS_DIR": tmp_path / "projects",
        "TEMP_DIR": tmp_path / "temp",
        "ORION_DATABASE_URL": sqlite_url_from_path(tmp_path / "speech.db"),
        "ORION_PRODUCTION_WORKER_ENABLED": False,
    }
    values.update(overrides)
    return Settings(**values)


async def test_composition_defaults_to_simulated_and_closes_provider(
    tmp_path: Path,
) -> None:
    container = build_production_container(_settings(tmp_path))
    assert container.speech_generation_provider.name == "orion-simulated-speech"
    assert container.speech_audio_store is not None
    assert container.speech_reconciler is not None
    await container.aclose()


def test_existing_stage_order_is_preserved() -> None:
    pipeline = StageRegistry.PIPELINE
    assert pipeline.index(ProductionStage.GENERATING_NARRATION) == (
        pipeline.index(ProductionStage.GENERATING_VIDEO_CLIPS) + 1
    )
