"""Versioned hybrid visual timeline contracts with deterministic image motion."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.domain.base import ContractModel
from backend.src.production.domain.path_rules import validate_relative_path
from backend.src.production.domain.visual_strategy import VisualMode, VisualMotionMode


class HybridVisualSourceKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"


class ImagePanDirection(StrEnum):
    LEFT_TO_RIGHT = "left_to_right"
    RIGHT_TO_LEFT = "right_to_left"
    TOP_TO_BOTTOM = "top_to_bottom"
    BOTTOM_TO_TOP = "bottom_to_top"


class HybridVisualAssetReference(ContractModel):
    asset_id: str = Field(min_length=1, max_length=200)
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(gt=0, le=250_000_000)
    width: int = Field(gt=0, le=16_384)
    height: int = Field(gt=0, le=16_384)
    source_kind: HybridVisualSourceKind
    source_duration_ms: int | None = Field(default=None, gt=0, le=3_600_000)

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = validate_relative_path(value)
        if "\\" in normalized:
            raise ValueError("hybrid visual path must use POSIX separators")
        return normalized

    @model_validator(mode="after")
    def validate_media(self) -> HybridVisualAssetReference:
        if self.source_kind is HybridVisualSourceKind.IMAGE:
            if not self.mime_type.startswith("image/"):
                raise ValueError("hybrid image source requires image MIME")
            if self.source_duration_ms is not None:
                raise ValueError("still image cannot claim source media duration")
        else:
            if self.mime_type != "video/mp4" or self.source_duration_ms is None:
                raise ValueError("hybrid video source requires MP4 duration")
        return self


class ImageMotionPlan(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    motion_mode: VisualMotionMode
    pan_direction: ImagePanDirection | None = None
    start_scale: Decimal = Field(ge=Decimal("1.0"), le=Decimal("1.2"))
    end_scale: Decimal = Field(ge=Decimal("1.0"), le=Decimal("1.2"))
    start_x_basis_points: int = Field(ge=0, le=10_000)
    start_y_basis_points: int = Field(ge=0, le=10_000)
    end_x_basis_points: int = Field(ge=0, le=10_000)
    end_y_basis_points: int = Field(ge=0, le=10_000)
    frame_count: int = Field(gt=0)
    output_width: int = Field(gt=0, le=16_384)
    output_height: int = Field(gt=0, le=16_384)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    def calculated_fingerprint(self) -> str:
        return _sha256_json(self.model_dump(mode="json", exclude={"fingerprint"}))

    @model_validator(mode="after")
    def validate_motion(self) -> ImageMotionPlan:
        if self.fingerprint != self.calculated_fingerprint():
            raise ValueError("image motion fingerprint differs")
        if self.motion_mode is VisualMotionMode.STATIC:
            if self.pan_direction is not None or self.start_scale != self.end_scale:
                raise ValueError("static image cannot declare movement")
        elif self.motion_mode in {VisualMotionMode.PAN, VisualMotionMode.PAN_AND_ZOOM}:
            if self.pan_direction is None:
                raise ValueError("panning image requires a deterministic direction")
        elif self.pan_direction is not None:
            raise ValueError("zoom-only image cannot declare pan direction")
        if self.motion_mode is VisualMotionMode.ZOOM_IN and self.end_scale <= self.start_scale:
            raise ValueError("zoom-in scale must increase")
        if self.motion_mode is VisualMotionMode.ZOOM_OUT and self.end_scale >= self.start_scale:
            raise ValueError("zoom-out scale must decrease")
        return self


class HybridVisualSegmentInput(ContractModel):
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    visual_mode: VisualMode
    motion_mode: VisualMotionMode
    usable_duration_ms: int = Field(gt=0, le=600_000)
    asset: HybridVisualAssetReference

    @model_validator(mode="after")
    def validate_mode(self) -> HybridVisualSegmentInput:
        image_mode = self.visual_mode in {VisualMode.GENERATED_IMAGE, VisualMode.REUSED_IMAGE}
        if image_mode != (self.asset.source_kind is HybridVisualSourceKind.IMAGE):
            raise ValueError("visual mode and hybrid source kind differ")
        if not image_mode and self.motion_mode is not VisualMotionMode.STATIC:
            raise ValueError("video segments cannot use local image motion")
        if (
            self.asset.source_kind is HybridVisualSourceKind.VIDEO
            and self.asset.source_duration_ms is not None
            and self.asset.source_duration_ms < self.usable_duration_ms
        ):
            raise ValueError("hybrid video source undercovers editorial duration")
        return self


class HybridVisualSegment(ContractModel):
    segment_id: str = Field(pattern=r"^hybrid-segment-[0-9]{4}$")
    sequence_index: int = Field(ge=0, le=5_000)
    shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    visual_mode: VisualMode
    motion_mode: VisualMotionMode
    asset: HybridVisualAssetReference
    timeline_start_frame: int = Field(ge=0)
    timeline_end_frame: int = Field(gt=0)
    timeline_start_ms: int = Field(ge=0, le=3_600_000)
    timeline_end_ms: int = Field(gt=0, le=3_600_000)
    playback_mode: Literal["once"] = "once"
    loop_count: Literal[1] = 1
    motion_plan: ImageMotionPlan | None = None

    @model_validator(mode="after")
    def validate_segment(self) -> HybridVisualSegment:
        if self.timeline_end_frame <= self.timeline_start_frame:
            raise ValueError("hybrid segment frame interval is empty")
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("hybrid segment time interval is empty")
        image = self.asset.source_kind is HybridVisualSourceKind.IMAGE
        if image != (self.motion_plan is not None):
            raise ValueError("only image segments require a motion plan")
        if self.motion_plan is not None:
            if self.motion_plan.motion_mode is not self.motion_mode:
                raise ValueError("segment motion differs from motion plan")
            if self.motion_plan.frame_count != self.timeline_end_frame - self.timeline_start_frame:
                raise ValueError("image motion frame count differs from timeline")
        return self


class HybridImageMotionCompositionPlan(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    job_id: UUID
    strategy_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    acquisition_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_width: int = Field(gt=0, le=16_384)
    output_height: int = Field(gt=0, le=16_384)
    frame_rate: int = Field(gt=0, le=120)
    expected_duration_frames: int = Field(gt=0)
    expected_duration_ms: int = Field(gt=0, le=3_600_000)
    segments: tuple[HybridVisualSegment, ...] = Field(min_length=1, max_length=5_000)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    def calculated_fingerprint(self) -> str:
        return _sha256_json(self.model_dump(mode="json", exclude={"fingerprint"}))

    @model_validator(mode="after")
    def validate_plan(self) -> HybridImageMotionCompositionPlan:
        if self.fingerprint != self.calculated_fingerprint():
            raise ValueError("hybrid composition fingerprint differs")
        if tuple(item.sequence_index for item in self.segments) != tuple(range(len(self.segments))):
            raise ValueError("hybrid segments must be canonically ordered")
        if self.segments[0].timeline_start_frame != 0:
            raise ValueError("hybrid timeline must start at frame zero")
        for before, after in zip(self.segments, self.segments[1:], strict=False):
            if before.timeline_end_frame != after.timeline_start_frame:
                raise ValueError("hybrid timeline contains a gap or overlap")
        if self.segments[-1].timeline_end_frame != self.expected_duration_frames:
            raise ValueError("hybrid timeline does not cover expected frames")
        if any(
            item.motion_plan is not None
            and (
                item.motion_plan.output_width != self.output_width
                or item.motion_plan.output_height != self.output_height
            )
            for item in self.segments
        ):
            raise ValueError("image motion geometry differs from output")
        return self


class HybridImageMotionRenderResult(ContractModel):
    """Provider-neutral integrity result for a locally realized visual track."""

    output_relative_path: str
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_rate: int = Field(gt=0)
    video_codec: Literal["h264"] = "h264"
    audio_stream_count: int = Field(ge=0, le=8)
    execution_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def build_hybrid_image_motion_plan(
    *,
    job_id: UUID,
    strategy_fingerprint: str,
    acquisition_fingerprint: str,
    inputs: tuple[HybridVisualSegmentInput, ...],
    output_width: int,
    output_height: int,
    frame_rate: int,
) -> HybridImageMotionCompositionPlan:
    if not inputs:
        raise ValueError("hybrid composition requires visual segments")
    cumulative_ms = 0
    segments: list[HybridVisualSegment] = []
    for index, item in enumerate(inputs):
        start_ms = cumulative_ms
        cumulative_ms += item.usable_duration_ms
        start_frame = _ms_to_frames(start_ms, frame_rate)
        end_frame = _ms_to_frames(cumulative_ms, frame_rate)
        frame_count = end_frame - start_frame
        if frame_count <= 0:
            raise ValueError("hybrid segment duration resolves to no frames")
        motion = (
            derive_image_motion_plan(
                shot_id=item.shot_id,
                source_sha256=item.asset.sha256,
                motion_mode=item.motion_mode,
                frame_count=frame_count,
                output_width=output_width,
                output_height=output_height,
            )
            if item.asset.source_kind is HybridVisualSourceKind.IMAGE
            else None
        )
        segments.append(
            HybridVisualSegment(
                segment_id=f"hybrid-segment-{index + 1:04d}",
                sequence_index=index,
                shot_id=item.shot_id,
                visual_mode=item.visual_mode,
                motion_mode=item.motion_mode,
                asset=item.asset,
                timeline_start_frame=start_frame,
                timeline_end_frame=end_frame,
                timeline_start_ms=_frames_to_ms(start_frame, frame_rate),
                timeline_end_ms=_frames_to_ms(end_frame, frame_rate),
                motion_plan=motion,
            )
        )
    expected_frames = _ms_to_frames(cumulative_ms, frame_rate)
    expected_ms = _frames_to_ms(expected_frames, frame_rate)
    resolved_segments = tuple(segments)
    provisional = HybridImageMotionCompositionPlan.model_construct(
        job_id=job_id,
        strategy_fingerprint=strategy_fingerprint,
        acquisition_fingerprint=acquisition_fingerprint,
        output_width=output_width,
        output_height=output_height,
        frame_rate=frame_rate,
        expected_duration_frames=expected_frames,
        expected_duration_ms=expected_ms,
        segments=resolved_segments,
        fingerprint="0" * 64,
    )
    return HybridImageMotionCompositionPlan(
        job_id=job_id,
        strategy_fingerprint=strategy_fingerprint,
        acquisition_fingerprint=acquisition_fingerprint,
        output_width=output_width,
        output_height=output_height,
        frame_rate=frame_rate,
        expected_duration_frames=expected_frames,
        expected_duration_ms=expected_ms,
        segments=resolved_segments,
        fingerprint=provisional.calculated_fingerprint(),
    )


def derive_image_motion_plan(
    *,
    shot_id: str,
    source_sha256: str,
    motion_mode: VisualMotionMode,
    frame_count: int,
    output_width: int,
    output_height: int,
) -> ImageMotionPlan:
    direction = None
    if motion_mode in {VisualMotionMode.PAN, VisualMotionMode.PAN_AND_ZOOM}:
        directions = tuple(ImagePanDirection)
        digest = hashlib.sha256(f"{shot_id}:{source_sha256}".encode()).digest()
        direction = directions[digest[0] % len(directions)]
    start_scale, end_scale = {
        VisualMotionMode.STATIC: (Decimal("1.0"), Decimal("1.0")),
        VisualMotionMode.PAN: (Decimal("1.08"), Decimal("1.08")),
        VisualMotionMode.ZOOM_IN: (Decimal("1.0"), Decimal("1.08")),
        VisualMotionMode.ZOOM_OUT: (Decimal("1.08"), Decimal("1.0")),
        VisualMotionMode.PAN_AND_ZOOM: (Decimal("1.02"), Decimal("1.08")),
    }[motion_mode]
    coordinates = _motion_coordinates(direction)
    provisional = ImageMotionPlan.model_construct(
        motion_mode=motion_mode,
        pan_direction=direction,
        start_scale=start_scale,
        end_scale=end_scale,
        start_x_basis_points=coordinates[0],
        start_y_basis_points=coordinates[1],
        end_x_basis_points=coordinates[2],
        end_y_basis_points=coordinates[3],
        frame_count=frame_count,
        output_width=output_width,
        output_height=output_height,
        source_sha256=source_sha256,
        fingerprint="0" * 64,
    )
    return ImageMotionPlan(
        motion_mode=motion_mode,
        pan_direction=direction,
        start_scale=start_scale,
        end_scale=end_scale,
        start_x_basis_points=coordinates[0],
        start_y_basis_points=coordinates[1],
        end_x_basis_points=coordinates[2],
        end_y_basis_points=coordinates[3],
        frame_count=frame_count,
        output_width=output_width,
        output_height=output_height,
        source_sha256=source_sha256,
        fingerprint=provisional.calculated_fingerprint(),
    )


def reconcile_hybrid_image_motion_plan(
    existing: HybridImageMotionCompositionPlan,
    proposed: HybridImageMotionCompositionPlan,
) -> HybridImageMotionCompositionPlan:
    if existing != proposed or existing.fingerprint != proposed.fingerprint:
        raise ValueError("hybrid image motion plan drifted during recovery")
    return existing


def serialize_hybrid_image_motion_plan(plan: HybridImageMotionCompositionPlan) -> bytes:
    return _canonical_json(plan.model_dump(mode="json"))


def deserialize_hybrid_image_motion_plan(content: bytes) -> HybridImageMotionCompositionPlan:
    value = json.loads(content.decode("utf-8", errors="strict"))
    if not isinstance(value, dict):
        raise ValueError("hybrid image motion plan must be an object")
    return HybridImageMotionCompositionPlan.model_validate(value)


def _motion_coordinates(
    direction: ImagePanDirection | None,
) -> tuple[int, int, int, int]:
    return {
        None: (5_000, 5_000, 5_000, 5_000),
        ImagePanDirection.LEFT_TO_RIGHT: (0, 5_000, 10_000, 5_000),
        ImagePanDirection.RIGHT_TO_LEFT: (10_000, 5_000, 0, 5_000),
        ImagePanDirection.TOP_TO_BOTTOM: (5_000, 0, 5_000, 10_000),
        ImagePanDirection.BOTTOM_TO_TOP: (5_000, 10_000, 5_000, 0),
    }[direction]


def _ms_to_frames(milliseconds: int, frame_rate: int) -> int:
    return (milliseconds * frame_rate + 500) // 1_000


def _frames_to_ms(frames: int, frame_rate: int) -> int:
    return (frames * 1_000 + frame_rate // 2) // frame_rate


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = [
    "HybridImageMotionCompositionPlan",
    "HybridVisualAssetReference",
    "HybridVisualSegment",
    "HybridVisualSegmentInput",
    "HybridVisualSourceKind",
    "ImageMotionPlan",
    "ImagePanDirection",
    "build_hybrid_image_motion_plan",
    "derive_image_motion_plan",
    "deserialize_hybrid_image_motion_plan",
    "reconcile_hybrid_image_motion_plan",
    "serialize_hybrid_image_motion_plan",
]
