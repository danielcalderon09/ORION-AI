"""Durable acceptance, recovery, and idempotency coverage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest

from backend.src.production.application.commands import StageCommand
from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.artifact import Artifact
from backend.src.production.domain.enums import ArtifactType, ProductionStage
from backend.src.production.media_composition.ports import MediaCompositionSource
from backend.src.production.render_validation.exceptions import FinalRenderCorruptError
from backend.src.production.render_validation.handler import FinalRenderValidationHandler
from backend.src.production.render_validation.models import FinalValidationStatus
from backend.src.production.render_validation.recovery import (
    prepared_manifest,
    validating_manifest,
)
from backend.src.production.render_validation.serialization import (
    deserialize_final_render_validation,
)
from backend.src.production.render_validation.source_reader import (
    VerifiedFinalRenderSourceReader,
)
from backend.src.production.render_validation.store import (
    LocalFinalRenderValidationStore,
)
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.fingerprints import canonical_sha256
from backend.src.production.rendering.handler import LocalRenderPreparationHandler
from backend.src.production.rendering.manifest_store import LocalRenderPreparationStore
from backend.src.production.rendering.models import RendererKind
from backend.src.production.rendering.output_probe import ProbedRenderOutput
from backend.src.production.rendering.process_runner import ControlledProcessResult
from backend.src.production.rendering.renderers import LocalFFmpegRenderer
from backend.src.production.runtime import create_simulated_handler_registry
from backend.src.production.runtime.context import StageContext
from backend.src.production.runtime.handlers import ValidationHandler
from backend.tests.unit.production.media_composition.conftest import make_source
from backend.tests.unit.production.rendering.conftest import (
    COMMAND_ID,
    NOW,
    StaticArtifactInventory,
    StaticVerifiedSourceReader,
    make_render_command_context,
    make_verified_source,
    write_verified_source,
)


class FakeRenderRunner:
    async def run(
        self,
        identity: Literal["ffmpeg", "ffprobe"],
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> ControlledProcessResult:
        assert timeout_seconds > 0
        if arguments == ("-version",):
            return ControlledProcessResult(
                identity,
                0,
                f"{identity} version 8.1.2-test".encode(),
                b"",
                1,
            )
        if identity == "ffmpeg":
            Path(arguments[-1]).write_bytes(b"durable-final-render-fixture")
            return ControlledProcessResult(identity, 0, b"", b"", 1)
        return ControlledProcessResult(
            identity,
            0,
            json.dumps(_probe_payload()).encode(),
            b"",
            1,
        )


class FakeFinalProbe:
    def __init__(self, output: ProbedRenderOutput | None = None) -> None:
        self.output = output or _probed_output()
        self.invocation_count = 0

    async def probe(self, source: object) -> ProbedRenderOutput:
        del source
        self.invocation_count += 1
        return self.output


@dataclass(frozen=True)
class RenderedFixture:
    artifacts: tuple[Artifact, ...]
    command: StageCommand
    context: StageContext


def _probe_payload() -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "24/1",
                "duration": "4.000000",
                "disposition": {"attached_pic": 0},
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"format_name": "mov,mp4", "duration": "4.000000"},
    }


def _probed_output() -> ProbedRenderOutput:
    normalized = {
        "duration_ms": 4_000,
        "width": 1280,
        "height": 720,
        "frame_rate_numerator": 24,
        "frame_rate_denominator": 1,
        "video_codec": "h264",
        "audio_codec": "aac",
        "pixel_format": "yuv420p",
        "video_stream_count": 1,
        "audio_stream_count": 1,
        "subtitle_stream_count": 0,
        "format_names": ("mov", "mp4"),
    }
    return ProbedRenderOutput(
        duration_ms=4_000,
        duration_frames=96,
        width=1280,
        height=720,
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        video_codec="h264",
        audio_codec="aac",
        pixel_format="yuv420p",
        video_stream_count=1,
        audio_stream_count=1,
        subtitle_stream_count=0,
        format_names=("mov", "mp4"),
        probe_fingerprint=canonical_sha256(normalized),
    )


def _real_source(workspace: Path) -> MediaCompositionSource:
    source = make_source()
    assets = []
    for asset in source.assets:
        content = f"fixture:{asset.asset_id}".encode()
        target = workspace.joinpath(*asset.relative_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        assets.append(
            asset.model_copy(
                update={
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                }
            )
        )
    return source.model_copy(update={"assets": tuple(assets)})


async def _rendered_fixture(workspace: Path) -> RenderedFixture:
    verified = make_verified_source(composition_source=_real_source(workspace))
    write_verified_source(workspace, verified)
    render_command, render_context = make_render_command_context()
    configuration = RenderingConfiguration(renderer=RendererKind.FFMPEG)
    render_output = await LocalRenderPreparationHandler(
        source_reader=StaticVerifiedSourceReader(verified),
        store=LocalRenderPreparationStore(
            workspace,
            max_request_bytes=configuration.max_request_bytes,
            max_manifest_bytes=configuration.max_manifest_bytes,
            max_execution_plan_bytes=configuration.max_execution_plan_bytes,
        ),
        renderer=LocalFFmpegRenderer(
            workspace_root=workspace,
            runner=FakeRenderRunner(),  # type: ignore[arg-type]
        ),
        configuration=configuration,
        clock=lambda: NOW,
    ).execute(render_command, render_context)
    assert render_output.result.outcome is StageOutcome.SUCCEEDED
    inputs = tuple(item.artifact_id for item in render_output.artifacts)
    command = StageCommand(
        command_id=COMMAND_ID,
        job_id=verified.plan_artifact.job_id,
        stage=ProductionStage.VALIDATING_RENDER,
        attempt_number=1,
        idempotency_key="final-render-validation:test",
        input_artifact_ids=inputs,
        created_at=NOW,
    )
    context = StageContext(
        job_id=command.job_id,
        command_id=command.command_id,
        stage=command.stage,
        attempt_number=command.attempt_number,
        input_artifact_ids=inputs,
        workspace_relative_path=(f"production/{command.job_id}/validating_render/attempt-1"),
        correlation_id=command.job_id,
    )
    return RenderedFixture(
        artifacts=(
            *render_output.artifacts,
            verified.plan_artifact,
            verified.manifest_artifact,
        ),
        command=command,
        context=context,
    )


def _handler(
    workspace: Path,
    fixture: RenderedFixture,
    probe: FakeFinalProbe,
) -> tuple[FinalRenderValidationHandler, LocalFinalRenderValidationStore]:
    store = LocalFinalRenderValidationStore(
        workspace_root=workspace,
        max_manifest_bytes=4_000_000,
    )
    reader = VerifiedFinalRenderSourceReader(
        workspace_root=workspace,
        inventory=StaticArtifactInventory(fixture.artifacts),
        max_json_bytes=4_000_000,
    )
    return (
        FinalRenderValidationHandler(
            source_reader=reader,
            store=store,
            probe=probe,
            clock=lambda: NOW,
        ),
        store,
    )


@pytest.mark.asyncio
async def test_correct_render_is_accepted_and_idempotent(tmp_path: Path) -> None:
    fixture = await _rendered_fixture(tmp_path)
    probe = FakeFinalProbe()
    handler, store = _handler(tmp_path, fixture, probe)
    first = await handler.execute(fixture.command, fixture.context)
    relative, _, _ = await store.manifest_identity(context=fixture.context)
    target = tmp_path.joinpath(*relative.split("/"))
    first_bytes = target.read_bytes()

    assert first.result.outcome is StageOutcome.SUCCEEDED
    assert tuple(item.artifact_type for item in first.artifacts) == (
        ArtifactType.FINAL_RENDER_VALIDATION,
    )
    manifest = deserialize_final_render_validation(first_bytes)
    assert manifest.status is FinalValidationStatus.VALIDATED
    assert manifest.ffprobe_summary is not None
    assert manifest.validation_fingerprint is not None
    assert probe.invocation_count == 1

    replay = await handler.execute(fixture.command, fixture.context)
    assert replay.result.outcome is StageOutcome.SUCCEEDED
    assert replay.artifacts == first.artifacts
    assert target.read_bytes() == first_bytes
    assert replay.result.metadata["ffprobe_revalidated"] is False
    assert probe.invocation_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["missing", "modified", "truncated"],
)
async def test_missing_or_changed_render_fails_without_deleting_it(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = await _rendered_fixture(tmp_path)
    render = next(
        item for item in fixture.artifacts if item.artifact_type is ArtifactType.LONG_FORM_RENDER
    )
    target = tmp_path.joinpath(*render.relative_path.split("/"))
    if mutation == "missing":
        target.unlink()
    elif mutation == "modified":
        target.write_bytes(target.read_bytes() + b"changed")
    else:
        target.write_bytes(b"x")
    handler, _ = _handler(tmp_path, fixture, FakeFinalProbe())
    output = await handler.execute(fixture.command, fixture.context)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert mutation == "missing" or target.exists()


@pytest.mark.asyncio
async def test_registered_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    fixture = await _rendered_fixture(tmp_path)
    artifacts = tuple(
        item.model_copy(update={"sha256": "0" * 64})
        if item.artifact_type is ArtifactType.LONG_FORM_RENDER
        else item
        for item in fixture.artifacts
    )
    fixture = replace(fixture, artifacts=artifacts)
    handler, _ = _handler(tmp_path, fixture, FakeFinalProbe())
    output = await handler.execute(fixture.command, fixture.context)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert output.result.error_code == "render_provenance_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("video_codec", "vp9"),
        ("frame_rate_numerator", 30),
        ("width", 640),
        ("duration_ms", 2_000),
        ("video_stream_count", 2),
    ],
)
async def test_probe_mismatch_is_durably_failed(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    fixture = await _rendered_fixture(tmp_path)
    probe = FakeFinalProbe(
        replace(_probed_output(), **{field: value})  # type: ignore[arg-type]
    )
    handler, store = _handler(tmp_path, fixture, probe)
    output = await handler.execute(fixture.command, fixture.context)
    manifest = await store.read_manifest(context=fixture.context)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert output.result.error_code == "final_probe_mismatch"
    assert manifest is not None and manifest.status is FinalValidationStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("artifact_type", "expected_code"),
    [
        (ArtifactType.RENDER_EXECUTION_MANIFEST, "render_inputs_missing"),
        (ArtifactType.MEDIA_COMPOSITION_PLAN, "composition_plan_missing"),
        (ArtifactType.FFMPEG_EXECUTION_PLAN, "render_inputs_missing"),
    ],
)
async def test_missing_provenance_is_rejected(
    tmp_path: Path,
    artifact_type: ArtifactType,
    expected_code: str,
) -> None:
    fixture = await _rendered_fixture(tmp_path)
    artifacts = tuple(item for item in fixture.artifacts if item.artifact_type is not artifact_type)
    fixture = replace(fixture, artifacts=artifacts)
    handler, _ = _handler(tmp_path, fixture, FakeFinalProbe())
    output = await handler.execute(fixture.command, fixture.context)
    assert output.result.outcome is StageOutcome.FAILED_PERMANENT
    assert output.result.error_code == expected_code


@pytest.mark.asyncio
async def test_interrupted_validating_manifest_resumes_only_probe(tmp_path: Path) -> None:
    fixture = await _rendered_fixture(tmp_path)
    probe = FakeFinalProbe()
    handler, store = _handler(tmp_path, fixture, probe)
    reader = VerifiedFinalRenderSourceReader(
        workspace_root=tmp_path,
        inventory=StaticArtifactInventory(fixture.artifacts),
        max_json_bytes=4_000_000,
    )
    source = await reader.read(
        context=fixture.context,
        input_artifact_ids=fixture.command.input_artifact_ids,
    )
    prepared = prepared_manifest(
        source=source,
        attempt_number=fixture.context.attempt_number,
        now=NOW,
    )
    await store.create_manifest(context=fixture.context, manifest=prepared)
    validating = validating_manifest(prepared, now=NOW)
    await store.checkpoint_manifest(
        context=fixture.context,
        previous=prepared,
        current=validating,
    )

    output = await handler.execute(fixture.command, fixture.context)
    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert probe.invocation_count == 1


@pytest.mark.asyncio
async def test_validated_replay_detects_later_render_change(tmp_path: Path) -> None:
    fixture = await _rendered_fixture(tmp_path)
    probe = FakeFinalProbe()
    handler, _ = _handler(tmp_path, fixture, probe)
    assert (await handler.execute(fixture.command, fixture.context)).result.outcome is (
        StageOutcome.SUCCEEDED
    )
    render = next(
        item for item in fixture.artifacts if item.artifact_type is ArtifactType.LONG_FORM_RENDER
    )
    target = tmp_path.joinpath(*render.relative_path.split("/"))
    target.write_bytes(target.read_bytes() + b"changed-after-validation")
    replay = await handler.execute(fixture.command, fixture.context)
    assert replay.result.outcome is StageOutcome.FAILED_PERMANENT
    assert replay.result.error_code == "validated_render_changed"
    assert target.exists()
    assert probe.invocation_count == 1


@pytest.mark.asyncio
async def test_corrupt_final_manifest_is_preserved_and_not_reprobed(tmp_path: Path) -> None:
    fixture = await _rendered_fixture(tmp_path)
    probe = FakeFinalProbe()
    handler, store = _handler(tmp_path, fixture, probe)
    assert (await handler.execute(fixture.command, fixture.context)).result.outcome is (
        StageOutcome.SUCCEEDED
    )
    relative, _, _ = await store.manifest_identity(context=fixture.context)
    target = tmp_path.joinpath(*relative.split("/"))
    corrupt = target.read_bytes().replace(b'"passed"', b'"failed"', 1)
    target.write_bytes(corrupt)

    replay = await handler.execute(fixture.command, fixture.context)
    assert replay.result.outcome is StageOutcome.FAILED_PERMANENT
    assert target.read_bytes() == corrupt
    assert probe.invocation_count == 1


def test_final_manifest_rejects_duplicate_json_keys() -> None:
    with pytest.raises(FinalRenderCorruptError):
        deserialize_final_render_validation(b'{"schema_version":"1.0.0","job_id":1,"job_id":2}')


def test_registry_injects_final_handler_without_changing_dry_run_default(
    tmp_path: Path,
) -> None:
    handler = FinalRenderValidationHandler(
        source_reader=VerifiedFinalRenderSourceReader(
            workspace_root=tmp_path,
            inventory=StaticArtifactInventory(()),
            max_json_bytes=4_000_000,
        ),
        store=LocalFinalRenderValidationStore(
            workspace_root=tmp_path,
            max_manifest_bytes=4_000_000,
        ),
        probe=FakeFinalProbe(),
        clock=lambda: NOW,
    )
    ids = iter(UUID(int=value) for value in range(1, 100))
    registry = create_simulated_handler_registry(
        clock=lambda: NOW,
        uuid_factory=lambda: next(ids),
        final_render_validation_handler=handler,
    )
    assert registry.resolve(ProductionStage.VALIDATING_RENDER) is handler

    default_registry = create_simulated_handler_registry(
        clock=lambda: NOW,
        uuid_factory=lambda: next(ids),
    )
    assert isinstance(
        default_registry.resolve(ProductionStage.VALIDATING_RENDER),
        ValidationHandler,
    )


def test_validation_command_id_is_stable_fixture() -> None:
    assert isinstance(COMMAND_ID, UUID)
