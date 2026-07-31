"""Deterministic allowlisted FFmpeg argument-plan construction."""

from __future__ import annotations

from typing import Literal

from backend.src.production.media_composition.domain.models import (
    CompositionAssetKind,
    CompositionClip,
    CompositionTransitionKind,
    MediaCompositionPlan,
)
from backend.src.production.rendering.configuration import RenderingConfiguration
from backend.src.production.rendering.exceptions import RenderingRequestError
from backend.src.production.rendering.fingerprints import canonical_sha256
from backend.src.production.rendering.models import (
    FFmpegExecutionPlan,
    FFmpegExecutionPolicy,
    LocalRenderRequest,
    RendererKind,
)
from backend.src.production.rendering.paths import ffmpeg_work_relative_path
from backend.src.production.rendering.ports import RenderStageContext


def build_ffmpeg_execution_plan(
    request: LocalRenderRequest,
    composition: MediaCompositionPlan,
    context: RenderStageContext,
    configuration: RenderingConfiguration,
) -> FFmpegExecutionPlan:
    if request.renderer_kind is not RendererKind.FFMPEG or request.dry_run:
        raise RenderingRequestError("FFmpeg execution requires an FFmpeg request")
    if composition.plan_fingerprint != request.source_plan_fingerprint:
        raise RenderingRequestError("execution plan source identity differs")
    unsupported = tuple(
        item.kind
        for item in composition.transitions
        if item.kind not in {CompositionTransitionKind.NONE, CompositionTransitionKind.CUT}
    )
    if unsupported:
        raise RenderingRequestError(f"transition type is unsupported: {unsupported[0].value}")
    asset_by_id = {item.asset_id: item for item in request.asset_fingerprints}
    args: list[str] = [
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file",
    ]
    input_indexes: list[tuple[CompositionClip, int]] = []
    index = 0
    for track in composition.tracks[:4]:
        for clip in track.clips:
            asset = asset_by_id.get(clip.asset_id)
            if asset is None:
                raise RenderingRequestError("timeline references an absent render asset")
            if clip.playback_mode == "loop":
                args.extend(("-stream_loop", str(clip.loop_count - 1)))
            args.extend(("-i", asset.relative_path))
            input_indexes.append((clip, index))
            index += 1
    subtitle_track = composition.tracks[4]
    subtitle_asset = None
    if request.requested_output.include_subtitles:
        if not subtitle_track.clips:
            raise RenderingRequestError("subtitle output requested without a subtitle asset")
        subtitle_asset = asset_by_id[subtitle_track.clips[0].asset_id]
        args.extend(("-i", subtitle_asset.relative_path))
        subtitle_index = index
    else:
        subtitle_index = None

    video_filters: list[str] = []
    video_labels: list[str] = []
    audio_filters: list[str] = []
    audio_labels: list[str] = []
    video_number = 0
    audio_number = 0
    for clip, input_index in input_indexes:
        kind = clip.kind
        duration = (clip.timeline_end_ms - clip.timeline_start_ms) / 1_000
        if kind is CompositionAssetKind.VIDEO:
            label = f"v{video_number}"
            source_start = clip.source_in_frame / request.frame_rate_numerator
            source_end_frame = clip.source_out_frame
            trim = f"start={_decimal(source_start)}"
            if source_end_frame is not None:
                trim += f":end={_decimal(source_end_frame / request.frame_rate_numerator)}"
            video_filters.append(
                f"[{input_index}:v]trim={trim},setpts=PTS-STARTPTS,"
                f"scale={request.output_width}:{request.output_height}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={request.output_width}:{request.output_height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1,fps={request.frame_rate_numerator}/"
                f"{request.frame_rate_denominator},format=yuv420p[{label}]"
            )
            video_labels.append(f"[{label}]")
            video_number += 1
            continue
        if kind not in {
            CompositionAssetKind.NARRATION,
            CompositionAssetKind.MUSIC,
            CompositionAssetKind.SOUND_EFFECT,
        }:
            continue
        label = f"a{audio_number}"
        envelope = clip.volume_envelope
        gain = envelope.base_gain_db if envelope is not None else 0
        chain = (
            f"[{input_index}:a]atrim=duration={_decimal(duration)},"
            f"asetpts=PTS-STARTPTS,volume={gain}dB"
        )
        if kind is CompositionAssetKind.MUSIC:
            if clip.fade_in_ms:
                chain += f",afade=t=in:st=0:d={_decimal(clip.fade_in_ms / 1_000)}"
            if clip.fade_out_ms:
                start = max(0, duration - clip.fade_out_ms / 1_000)
                chain += f",afade=t=out:st={_decimal(start)}:d={_decimal(clip.fade_out_ms / 1_000)}"
            for instruction in composition.ducking:
                delta = instruction.target_gain_db - gain
                chain += (
                    f",volume={delta}dB:enable='between(t,"
                    f"{_decimal(instruction.start_ms / 1_000)},"
                    f"{_decimal(instruction.end_ms / 1_000)})'"
                )
        delay = clip.timeline_start_ms
        chain += f",adelay={delay}:all=1[{label}]"
        audio_filters.append(chain)
        audio_labels.append(f"[{label}]")
        audio_number += 1
    if not video_labels:
        raise RenderingRequestError("FFmpeg composition requires video")
    if not audio_labels:
        raise RenderingRequestError("FFmpeg composition requires planned audio")
    video_filters.append("".join(video_labels) + f"concat=n={len(video_labels)}:v=1:a=0[vout]")
    audio_filters.append(
        "".join(audio_labels)
        + f"amix=inputs={len(audio_labels)}:duration=longest:normalize=0[aout]"
    )
    filter_graph = ";".join((*video_filters, *audio_filters))
    args.extend(("-filter_complex", filter_graph, "-map", "[vout]", "-map", "[aout]"))
    if subtitle_index is not None:
        args.extend(("-map", f"{subtitle_index}:s:0"))
    args.extend(
        (
            "-r",
            f"{request.frame_rate_numerator}/{request.frame_rate_denominator}",
            "-c:v",
            "libx264",
            "-preset",
            configuration.video_preset,
            "-crf",
            str(configuration.video_crf),
            "-pix_fmt",
            configuration.pixel_format,
            "-c:a",
            "aac",
            "-b:a",
            configuration.audio_bitrate,
        )
    )
    if subtitle_index is not None:
        args.extend(("-c:s", "mov_text"))
    args.extend(
        (
            "-t",
            _decimal(request.expected_duration_ms / 1_000),
            "-movflags",
            "+faststart",
            "-map_metadata",
            "-1",
            "-y",
        )
    )
    work = ffmpeg_work_relative_path(context, request.request_fingerprint)
    partial = f"{work}/output.partial.mp4"
    args.append(partial)
    policy = FFmpegExecutionPolicy(
        video_preset=configuration.video_preset,
        video_crf=configuration.video_crf,
        audio_bitrate=configuration.audio_bitrate,
        process_timeout_seconds=configuration.process_timeout_seconds,
        probe_timeout_seconds=configuration.probe_timeout_seconds,
        max_stderr_bytes=configuration.max_stderr_bytes,
        max_output_bytes=configuration.max_output_bytes,
        duration_tolerance_ms=configuration.duration_tolerance_ms,
        frame_rate_tolerance=configuration.frame_rate_tolerance,
    )
    filter_fingerprint = canonical_sha256(filter_graph)
    subtitle_strategy: Literal["none", "mux_mov_text"] = (
        "mux_mov_text" if subtitle_index is not None else "none"
    )
    data = {
        "argument_vector": args,
        "execution_policy": policy.model_dump(mode="json"),
        "filter_complex_fingerprint": filter_fingerprint,
        "input_assets": [item.model_dump(mode="json") for item in request.asset_fingerprints],
        "output_relative_path": request.requested_output.relative_path,
        "request_fingerprint": request.request_fingerprint,
        "subtitle_strategy": subtitle_strategy,
        "temporary_output_relative_path": partial,
        "temporary_workspace_relative_path": work,
    }
    return FFmpegExecutionPlan(
        request_fingerprint=request.request_fingerprint,
        input_assets=request.asset_fingerprints,
        temporary_workspace_relative_path=work,
        temporary_output_relative_path=partial,
        output_relative_path=request.requested_output.relative_path,
        expected_output=request.requested_output,
        argument_vector=tuple(args),
        argument_fingerprint=canonical_sha256(data),
        execution_policy=policy,
        filter_complex_fingerprint=filter_fingerprint,
        subtitle_strategy=subtitle_strategy,
        metadata={
            "absolute_paths_persisted": False,
            "command_shell": False,
            "transition_strategy": "cuts",
        },
    )


def execution_plan_fingerprint(plan: FFmpegExecutionPlan) -> str:
    return canonical_sha256(
        {
            "argument_vector": list(plan.argument_vector),
            "execution_policy": plan.execution_policy.model_dump(mode="json"),
            "filter_complex_fingerprint": plan.filter_complex_fingerprint,
            "input_assets": [item.model_dump(mode="json") for item in plan.input_assets],
            "output_relative_path": plan.output_relative_path,
            "request_fingerprint": plan.request_fingerprint,
            "subtitle_strategy": plan.subtitle_strategy,
            "temporary_output_relative_path": plan.temporary_output_relative_path,
            "temporary_workspace_relative_path": plan.temporary_workspace_relative_path,
        }
    )


def _decimal(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"
