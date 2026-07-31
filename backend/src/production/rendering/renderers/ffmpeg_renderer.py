"""Controlled local FFmpeg renderer with FFprobe-gated promotion."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from backend.src.production.binary_assets.exceptions import BinaryAssetError
from backend.src.production.binary_assets.workspace import WorkspaceConfinement
from backend.src.production.media_composition.domain.models import CompositionAssetKind
from backend.src.production.rendering.exceptions import (
    RenderingConflictError,
    RenderingProcessError,
    RenderingValidationError,
)
from backend.src.production.rendering.executable_resolver import (
    probe_media_executable_versions,
)
from backend.src.production.rendering.models import (
    FFmpegExecutionPlan,
    FFmpegRenderResult,
    LocalRenderRequest,
    RendererCapabilities,
    RendererKind,
)
from backend.src.production.rendering.output_probe import (
    ProbedRenderOutput,
    probe_render_output,
)
from backend.src.production.rendering.process_runner import ControlledMediaProcessRunner

FFMPEG_RENDERER_CONTRACT_VERSION = "1.0.0"
_ALLOWED_MIME_TYPES = {
    CompositionAssetKind.VIDEO: frozenset({"video/mp4"}),
    CompositionAssetKind.NARRATION: frozenset({"audio/wav", "audio/x-wav"}),
    CompositionAssetKind.MUSIC: frozenset({"audio/wav", "audio/x-wav"}),
    CompositionAssetKind.SOUND_EFFECT: frozenset({"audio/wav", "audio/x-wav"}),
    CompositionAssetKind.SUBTITLES: frozenset({"application/x-subrip", "text/plain", "text/srt"}),
}


class LocalFFmpegRenderer:
    def __init__(
        self,
        *,
        workspace_root: Path,
        runner: ControlledMediaProcessRunner,
    ) -> None:
        self._confinement = WorkspaceConfinement(workspace_root)
        self._runner = runner
        self._closed = False
        self.invocation_count = 0

    @property
    def renderer_kind(self) -> RendererKind:
        return RendererKind.FFMPEG

    @property
    def capabilities(self) -> RendererCapabilities:
        return RendererCapabilities(
            renderer_kind=RendererKind.FFMPEG,
            renderer_version=FFMPEG_RENDERER_CONTRACT_VERSION,
            produces_media=True,
            supported_container_formats=("mp4",),
            supported_video_codecs=("h264",),
            supported_audio_codecs=("aac",),
            supports_video_tracks=True,
            supports_narration=True,
            supports_music=True,
            supports_sound_effects=True,
            supports_subtitles=True,
            supports_transitions=True,
            supports_volume_envelopes=True,
            supports_ducking=True,
            supports_fades=True,
            supports_vertical_video=True,
            max_width=16_384,
            max_height=16_384,
            max_frame_rate=120,
            deterministic_preparation=True,
        )

    async def prepare_or_validate(
        self,
        request: LocalRenderRequest,
        execution_plan: FFmpegExecutionPlan | None = None,
    ) -> FFmpegRenderResult:
        if self._closed:
            raise RenderingProcessError("renderer_closed", "local FFmpeg renderer is closed")
        if request.renderer_kind is not RendererKind.FFMPEG or request.dry_run:
            raise RenderingValidationError("FFmpeg renderer received another renderer request")
        if execution_plan is None or execution_plan.request_fingerprint != (
            request.request_fingerprint
        ):
            raise RenderingValidationError("FFmpeg execution plan is missing or stale")
        paths = await asyncio.to_thread(self._verify_assets, execution_plan)
        versions = await probe_media_executable_versions(self._runner)
        work = self._resolve(execution_plan.temporary_workspace_relative_path)
        partial = self._resolve(execution_plan.temporary_output_relative_path)
        final = self._resolve(execution_plan.output_relative_path)
        await asyncio.to_thread(self._prepare_work, work, partial)
        self.invocation_count += 1
        try:
            inspected: ProbedRenderOutput | None = None
            if partial.exists():
                try:
                    inspected = await self._probe_partial(
                        partial,
                        request=request,
                        plan=execution_plan,
                    )
                except RenderingValidationError:
                    await asyncio.to_thread(self._remove_owned_partial, partial, work)
            if inspected is None:
                runtime_arguments = self._runtime_arguments(
                    execution_plan,
                    asset_paths=paths,
                    partial=partial,
                )
                result = await self._runner.run(
                    "ffmpeg",
                    runtime_arguments,
                    timeout_seconds=execution_plan.execution_policy.process_timeout_seconds,
                )
                if result.return_code != 0:
                    await asyncio.to_thread(self._remove_owned_partial, partial, work)
                    raise RenderingProcessError(
                        "ffmpeg_nonzero",
                        "FFmpeg did not produce a render",
                    )
                inspected = await self._probe_partial(
                    partial,
                    request=request,
                    plan=execution_plan,
                )
            size, digest = await asyncio.to_thread(
                self._validate_file_and_hash,
                partial,
                execution_plan.execution_policy.max_output_bytes,
            )
            await asyncio.to_thread(self._promote, partial, final)
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self._remove_owned_partial, partial, work))
            raise
        except Exception:
            if partial.exists():
                await asyncio.to_thread(self._remove_owned_partial, partial, work)
            raise
        return FFmpegRenderResult(
            renderer_version=versions.ffmpeg,
            ffprobe_version=versions.ffprobe,
            request_fingerprint=request.request_fingerprint,
            output_relative_path=execution_plan.output_relative_path,
            output_size_bytes=size,
            output_sha256=digest,
            duration_ms=inspected.duration_ms,
            duration_frames=inspected.duration_frames,
            width=inspected.width,
            height=inspected.height,
            frame_rate_numerator=inspected.frame_rate_numerator,
            frame_rate_denominator=inspected.frame_rate_denominator,
            video_codec="h264",
            audio_codec="aac",
            pixel_format="yuv420p",
            audio_stream_count=inspected.audio_stream_count,
            subtitle_stream_count=inspected.subtitle_stream_count,
            probe_fingerprint=inspected.probe_fingerprint,
            validation_codes=(
                "asset_checksums_verified",
                "ffmpeg_exit_zero",
                "ffprobe_output_validated",
                "output_atomically_promoted",
            ),
            diagnostic_codes=(),
            metadata={
                "network": False,
                "shell": False,
                "validated_by_ffprobe": True,
            },
        )

    async def _probe_partial(
        self,
        partial: Path,
        *,
        request: LocalRenderRequest,
        plan: FFmpegExecutionPlan,
    ) -> ProbedRenderOutput:
        await asyncio.to_thread(
            self._validate_file_and_hash,
            partial,
            plan.execution_policy.max_output_bytes,
        )
        return await probe_render_output(
            runner=self._runner,
            path=partial,
            request=request,
            plan=plan,
        )

    def _verify_assets(self, plan: FFmpegExecutionPlan) -> dict[str, Path]:
        results: dict[str, Path] = {}
        for asset in plan.input_assets:
            if asset.size_bytes is None or asset.mime_type is None:
                raise RenderingValidationError(
                    "FFmpeg assets require durable size and MIME metadata"
                )
            if asset.mime_type not in _ALLOWED_MIME_TYPES[asset.media_kind]:
                raise RenderingValidationError("render asset MIME type is unsupported")
            path = self._resolve(asset.relative_path, require_exists=True)
            size, digest = self._validate_file_and_hash(path, asset.size_bytes)
            if size != asset.size_bytes or digest != asset.sha256:
                raise RenderingValidationError("render asset changed after planning")
            results[asset.relative_path] = path
        return results

    def _runtime_arguments(
        self,
        plan: FFmpegExecutionPlan,
        *,
        asset_paths: dict[str, Path],
        partial: Path,
    ) -> tuple[str, ...]:
        substitutions = {key: str(value) for key, value in asset_paths.items()}
        substitutions[plan.temporary_output_relative_path] = str(partial)
        return tuple(substitutions.get(item, item) for item in plan.argument_vector)

    def _prepare_work(self, work: Path, partial: Path) -> None:
        if work.exists():
            self._confinement.reject_unsafe_components(work)
        else:
            work.mkdir(parents=True, exist_ok=False)
            self._confinement.reject_unsafe_components(work)
        if partial.exists():
            self._confinement.reject_unsafe_file(partial)

    def _promote(self, partial: Path, final: Path) -> None:
        if final.exists() or final.is_symlink():
            raise RenderingConflictError("final render output already exists")
        final.parent.mkdir(parents=True, exist_ok=True)
        self._confinement.reject_unsafe_components(final.parent)
        if final.exists() or final.is_symlink():
            raise RenderingConflictError("final render output appeared concurrently")
        os.replace(partial, final)
        self._confinement.reject_unsafe_file(final)
        _fsync_directory(final.parent)

    def _remove_owned_partial(self, partial: Path, work: Path) -> None:
        if partial.parent != work:
            raise RenderingConflictError("temporary render output ownership differs")
        if partial.exists():
            self._confinement.reject_unsafe_file(partial)
            partial.unlink()

    def _validate_file_and_hash(self, path: Path, maximum: int) -> tuple[int, str]:
        try:
            self._confinement.reject_unsafe_file(path)
            status = path.stat()
            if not 1 <= status.st_size <= maximum or status.st_nlink != 1:
                raise RenderingValidationError("render output size or link count is invalid")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1_048_576):
                    digest.update(chunk)
        except RenderingValidationError:
            raise
        except (BinaryAssetError, OSError) as exc:
            raise RenderingValidationError("render media file is unsafe") from exc
        return status.st_size, digest.hexdigest()

    def _resolve(self, relative_path: str, *, require_exists: bool = False) -> Path:
        try:
            return self._confinement.resolve(
                relative_path,
                require_exists=require_exists,
            )
        except Exception as exc:
            raise RenderingValidationError("render path is outside the workspace") from exc

    async def close(self) -> None:
        self._closed = True


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
