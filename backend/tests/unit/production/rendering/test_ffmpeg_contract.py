"""Pure and fake-runner coverage for the controlled local FFmpeg boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import pytest

from backend.src.production.application.results import StageOutcome
from backend.src.production.domain.enums import ArtifactType
from backend.src.production.media_composition.domain.models import (
    CompositionAssetKind,
    CompositionTransitionKind,
)
from backend.src.production.media_composition.ports import MediaCompositionSource
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.exceptions import (
    RenderingConflictError,
    RenderingCorruptError,
    RenderingExecutableError,
    RenderingRequestError,
)
from backend.src.production.rendering.executable_resolver import (
    LocalMediaExecutableResolver,
    probe_media_executable_versions,
)
from backend.src.production.rendering.execution_plan import (
    build_ffmpeg_execution_plan,
)
from backend.src.production.rendering.handler import LocalRenderPreparationHandler
from backend.src.production.rendering.manifest_store import LocalRenderPreparationStore
from backend.src.production.rendering.models import RendererKind
from backend.src.production.rendering.process_runner import ControlledProcessResult
from backend.src.production.rendering.renderers import LocalFFmpegRenderer
from backend.src.production.rendering.request_builder import build_local_render_request
from backend.src.production.rendering.serialization import (
    deserialize_ffmpeg_execution_plan,
    serialize_ffmpeg_execution_plan,
)
from backend.tests.unit.production.media_composition.conftest import make_source
from backend.tests.unit.production.rendering.conftest import (
    NOW,
    StaticVerifiedSourceReader,
    make_render_command_context,
    make_verified_source,
)


class FakeControlledRunner:
    def __init__(
        self,
        *,
        ffmpeg_return_code: int = 0,
        wrong_width: bool = False,
        output_duration_seconds: float = 4.0,
    ) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.ffmpeg_return_code = ffmpeg_return_code
        self.wrong_width = wrong_width
        self.output_duration_seconds = output_duration_seconds

    async def run(
        self,
        identity: Literal["ffmpeg", "ffprobe"],
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> ControlledProcessResult:
        assert timeout_seconds > 0
        self.calls.append((identity, arguments))
        if arguments == ("-version",):
            banner = f"{identity} version 8.1.2-test Copyright build C:/private/user"
            return ControlledProcessResult(identity, 0, banner.encode(), b"", 1)
        if identity == "ffmpeg":
            if self.ffmpeg_return_code == 0:
                Path(arguments[-1]).write_bytes(b"deterministic-fake-mp4-content")
            return ControlledProcessResult(identity, self.ffmpeg_return_code, b"", b"bounded", 2)
        payload = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "pix_fmt": "yuv420p",
                    "width": 640 if self.wrong_width else 1280,
                    "height": 720,
                    "avg_frame_rate": "24/1",
                    "duration": f"{self.output_duration_seconds:.6f}",
                    "disposition": {"attached_pic": 0},
                },
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {
                "format_name": "mov,mp4",
                "duration": f"{self.output_duration_seconds:.6f}",
            },
        }
        return ControlledProcessResult(
            identity,
            0,
            json.dumps(payload).encode(),
            b"",
            1,
        )


def _real_asset_source(
    workspace: Path,
    source: MediaCompositionSource | None = None,
) -> MediaCompositionSource:
    source = source or make_source()
    assets = []
    for asset in source.assets:
        target = workspace.joinpath(*asset.relative_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        content = f"fixture:{asset.asset_id}".encode()
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


def _fifteen_second_looping_source() -> MediaCompositionSource:
    source = make_source()
    assets = []
    for asset in source.assets:
        if asset.kind is CompositionAssetKind.VIDEO:
            assets.append(asset.model_copy(update={"duration_ms": 4_000, "frame_count": 96}))
        elif asset.kind is CompositionAssetKind.NARRATION:
            assets.append(asset.model_copy(update={"duration_ms": 15_000, "frame_count": 360_000}))
    asset_ids = {asset.asset_id for asset in assets}
    return source.model_copy(
        update={
            "assets": tuple(sorted(assets, key=lambda item: item.asset_id)),
            "asset_validation": tuple(
                item for item in source.asset_validation if item.asset_id in asset_ids
            ),
            "shots": (
                source.shots[0].model_copy(update={"shot_start_ms": 0, "shot_end_ms": 7_500}),
                source.shots[1].model_copy(update={"shot_start_ms": 7_500, "shot_end_ms": 15_000}),
            ),
            "narration": (
                source.narration[0].model_copy(
                    update={"timeline_start_ms": 0, "duration_ms": 15_000}
                ),
            ),
            "music": None,
            "sound_effects": (),
        }
    )


def test_configuration_is_closed_and_bounded() -> None:
    assert RenderingConfiguration(renderer="dry_run").renderer is RendererKind.DRY_RUN
    assert RenderingConfiguration(renderer="ffmpeg").renderer is RendererKind.FFMPEG
    for renderer in ("davinci_resolve", "other"):
        with pytest.raises(ValueError):
            RenderingConfiguration(renderer=renderer)
    for values in (
        {"video_crf": -1},
        {"video_crf": 36},
        {"process_timeout_seconds": 0},
        {"max_output_bytes": 1},
        {"video_preset": "user-value"},
        {"audio_bitrate": "arbitrary"},
    ):
        with pytest.raises(ValueError):
            RenderingConfiguration(**values)
    assert "extra_args" not in RenderingConfiguration.model_fields


def test_executable_resolver_accepts_only_exact_regular_binaries(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"x")
    ffprobe.write_bytes(b"x")
    resolved = LocalMediaExecutableResolver().resolve(
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
    )
    assert resolved.ffmpeg == ffmpeg.resolve()
    assert resolved.ffprobe == ffprobe.resolve()
    wrong = tmp_path / "encoder.exe"
    wrong.write_bytes(b"x")
    with pytest.raises(RenderingExecutableError, match="identity"):
        LocalMediaExecutableResolver().resolve(
            ffmpeg_path=wrong,
            ffprobe_path=ffprobe,
        )
    directory = tmp_path / "ffmpeg"
    directory.mkdir()
    with pytest.raises(RenderingExecutableError, match="regular"):
        LocalMediaExecutableResolver().resolve(
            ffmpeg_path=directory,
            ffprobe_path=ffprobe,
        )


@pytest.mark.asyncio
async def test_version_probe_normalizes_and_excludes_banner() -> None:
    runner = FakeControlledRunner()
    versions = await probe_media_executable_versions(runner)  # type: ignore[arg-type]
    assert versions.ffmpeg == "8.1.2"
    assert versions.ffprobe == "8.1.2"
    assert "private" not in versions.ffmpeg
    assert [item[1] for item in runner.calls] == [("-version",), ("-version",)]


def test_ffmpeg_request_and_execution_plan_are_deterministic(tmp_path: Path) -> None:
    source = make_verified_source()
    dry = build_local_render_request(source, RenderingConfiguration())
    configuration = RenderingConfiguration(renderer=RendererKind.FFMPEG)
    ffmpeg = build_local_render_request(source, configuration)
    assert ffmpeg.request_fingerprint != dry.request_fingerprint
    alternate = build_local_render_request(
        source,
        RenderingConfiguration(renderer="ffmpeg", video_crf=21),
    )
    assert alternate.request_fingerprint != ffmpeg.request_fingerprint
    assert ffmpeg.requested_output == dry.requested_output
    _, context = make_render_command_context()
    first = build_ffmpeg_execution_plan(ffmpeg, source.plan, context, configuration)
    second = build_ffmpeg_execution_plan(ffmpeg, source.plan, context, configuration)
    assert first == second
    assert first.argument_vector[-1].endswith("/output.partial.mp4")
    assert all(not Path(item).is_absolute() for item in first.argument_vector)
    assert "shell" not in " ".join(first.argument_vector).lower()
    assert "http://" not in " ".join(first.argument_vector)
    assert str(tmp_path) not in first.argument_fingerprint
    content = serialize_ffmpeg_execution_plan(first)
    assert deserialize_ffmpeg_execution_plan(content) == first
    corrupted = content.replace(b'"-nostdin"', b'"-stdin"', 1)
    with pytest.raises(RenderingCorruptError, match="fingerprint"):
        deserialize_ffmpeg_execution_plan(corrupted)
    transition = source.plan.transitions[0].model_copy(
        update={"kind": CompositionTransitionKind.DISSOLVE}
    )
    unsupported = source.plan.model_copy(
        update={"transitions": (transition, *source.plan.transitions[1:])}
    )
    with pytest.raises(RenderingRequestError, match="unsupported"):
        build_ffmpeg_execution_plan(ffmpeg, unsupported, context, configuration)


def test_ffmpeg_plan_trims_looped_video_to_fifteen_second_timeline() -> None:
    verified = make_verified_source(composition_source=_fifteen_second_looping_source())
    configuration = RenderingConfiguration(renderer=RendererKind.FFMPEG)
    request = build_local_render_request(verified, configuration)
    _, context = make_render_command_context()

    plan = build_ffmpeg_execution_plan(request, verified.plan, context, configuration)
    filters = plan.argument_vector[plan.argument_vector.index("-filter_complex") + 1]

    assert request.expected_duration_ms == 15_000
    assert request.expected_duration_frames == 360
    assert plan.argument_vector.count("-stream_loop") == 2
    assert filters.count("trim=start=0:duration=7.5") == 2
    assert "trim=start=0:end=4" not in filters
    assert plan.argument_vector[plan.argument_vector.index("-t") + 1] == "15"


@pytest.mark.asyncio
async def test_fake_ffmpeg_preparation_accepts_fifteen_second_looped_video(
    tmp_path: Path,
) -> None:
    composition_source = _real_asset_source(
        tmp_path,
        _fifteen_second_looping_source(),
    )
    source = make_verified_source(composition_source=composition_source)
    command, context = make_render_command_context()
    configuration = RenderingConfiguration(renderer=RendererKind.FFMPEG)
    runner = FakeControlledRunner(output_duration_seconds=15.0)
    store = LocalRenderPreparationStore(
        tmp_path,
        max_request_bytes=configuration.max_request_bytes,
        max_manifest_bytes=configuration.max_manifest_bytes,
        max_execution_plan_bytes=configuration.max_execution_plan_bytes,
    )

    output = await LocalRenderPreparationHandler(
        source_reader=StaticVerifiedSourceReader(source),
        store=store,
        renderer=LocalFFmpegRenderer(
            workspace_root=tmp_path,
            runner=runner,  # type: ignore[arg-type]
        ),
        configuration=configuration,
        clock=lambda: NOW,
    ).execute(command, context)

    assert output.result.outcome is StageOutcome.SUCCEEDED
    assert output.result.metadata["media_produced"] is True
    assert [identity for identity, _ in runner.calls] == [
        "ffmpeg",
        "ffprobe",
        "ffmpeg",
        "ffprobe",
    ]


@pytest.mark.asyncio
async def test_execution_plan_store_is_write_once(tmp_path: Path) -> None:
    source = make_verified_source()
    configuration = RenderingConfiguration(renderer="ffmpeg")
    request = build_local_render_request(source, configuration)
    _, context = make_render_command_context()
    plan = build_ffmpeg_execution_plan(request, source.plan, context, configuration)
    store = LocalRenderPreparationStore(
        tmp_path,
        max_request_bytes=configuration.max_request_bytes,
        max_manifest_bytes=configuration.max_manifest_bytes,
        max_execution_plan_bytes=configuration.max_execution_plan_bytes,
    )
    first = await store.write_execution_plan(context=context, plan=plan)
    assert await store.write_execution_plan(context=context, plan=plan) == first
    changed_configuration = RenderingConfiguration(renderer="ffmpeg", video_crf=21)
    changed_request = build_local_render_request(source, changed_configuration)
    changed = build_ffmpeg_execution_plan(
        changed_request,
        source.plan,
        context,
        changed_configuration,
    )
    with pytest.raises(RenderingConflictError):
        await store.write_execution_plan(context=context, plan=changed)


@pytest.mark.asyncio
async def test_fake_ffmpeg_handler_emits_video_only_after_probe(tmp_path: Path) -> None:
    composition_source = _real_asset_source(tmp_path)
    source = make_verified_source(composition_source=composition_source)
    command, context = make_render_command_context()
    configuration = RenderingConfiguration(renderer="ffmpeg")
    runner = FakeControlledRunner()
    renderer = LocalFFmpegRenderer(
        workspace_root=tmp_path,
        runner=runner,  # type: ignore[arg-type]
    )
    store = LocalRenderPreparationStore(
        tmp_path,
        max_request_bytes=configuration.max_request_bytes,
        max_manifest_bytes=configuration.max_manifest_bytes,
        max_execution_plan_bytes=configuration.max_execution_plan_bytes,
    )
    handler = LocalRenderPreparationHandler(
        source_reader=StaticVerifiedSourceReader(source),
        store=store,
        renderer=renderer,
        configuration=configuration,
        clock=lambda: NOW,
    )
    output = await handler.execute(command, context)
    assert output.result.outcome is StageOutcome.SUCCEEDED
    types = tuple(item.artifact_type for item in output.artifacts)
    assert types == (
        ArtifactType.LOCAL_RENDER_REQUEST,
        ArtifactType.FFMPEG_EXECUTION_PLAN,
        ArtifactType.RENDER_EXECUTION_MANIFEST,
        ArtifactType.LONG_FORM_RENDER,
    )
    video = output.artifacts[-1]
    assert video.size_bytes > 0
    assert (
        video.sha256
        == hashlib.sha256(
            tmp_path.joinpath(*video.relative_path.split("/")).read_bytes()
        ).hexdigest()
    )
    assert video.metadata["validated_by_ffprobe"] is True
    assert [identity for identity, _ in runner.calls] == [
        "ffmpeg",
        "ffprobe",
        "ffmpeg",
        "ffprobe",
    ]
    replay = await handler.execute(command, context)
    assert replay.result.outcome is StageOutcome.SUCCEEDED
    assert renderer.invocation_count == 1
    assert len(runner.calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runner", "expected_code"),
    [
        (FakeControlledRunner(ffmpeg_return_code=1), "ffmpeg_nonzero"),
        (FakeControlledRunner(wrong_width=True), "render_preparation_invalid"),
    ],
)
async def test_ffmpeg_failure_never_emits_final_video(
    tmp_path: Path,
    runner: FakeControlledRunner,
    expected_code: str,
) -> None:
    source = make_verified_source(composition_source=_real_asset_source(tmp_path))
    command, context = make_render_command_context()
    configuration = RenderingConfiguration(renderer="ffmpeg")
    store = LocalRenderPreparationStore(
        tmp_path,
        max_request_bytes=configuration.max_request_bytes,
        max_manifest_bytes=configuration.max_manifest_bytes,
        max_execution_plan_bytes=configuration.max_execution_plan_bytes,
    )
    output = await LocalRenderPreparationHandler(
        source_reader=StaticVerifiedSourceReader(source),
        store=store,
        renderer=LocalFFmpegRenderer(
            workspace_root=tmp_path,
            runner=runner,  # type: ignore[arg-type]
        ),
        configuration=configuration,
        clock=lambda: NOW,
    ).execute(command, context)
    assert output.result.outcome is not StageOutcome.SUCCEEDED
    assert output.result.error_code == expected_code
    assert not output.artifacts
    request = build_local_render_request(source, configuration)
    final = tmp_path.joinpath(*request.requested_output.relative_path.split("/"))
    assert not final.exists()
    assert not tuple(tmp_path.rglob("*.partial.mp4"))
