"""Pure audio-first duration resolution shared by composition and future pre-video planning."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from pydantic import Field, model_validator

from backend.src.production.domain.base import ContractModel


class DurationResolutionError(ValueError):
    """Raised when natural media duration exceeds the authorized target tolerance."""

    def __init__(self, message: str, *, resolution: AudioFirstDurationResolution) -> None:
        self.resolution = resolution
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DurationResolutionPolicy:
    maximum_absolute_extension_ms: int = 3_000
    maximum_relative_extension_ratio: Decimal = Decimal("0.20")

    def __post_init__(self) -> None:
        if self.maximum_absolute_extension_ms < 0:
            raise ValueError("maximum absolute duration extension cannot be negative")
        if not Decimal("0") <= self.maximum_relative_extension_ratio <= Decimal("1"):
            raise ValueError("maximum relative duration extension must be between zero and one")

    def maximum_allowed_duration_ms(self, requested_target_duration_ms: int) -> int:
        if requested_target_duration_ms <= 0:
            raise ValueError("requested target duration must be positive")
        absolute_limit = requested_target_duration_ms + self.maximum_absolute_extension_ms
        relative_limit = int(
            (
                Decimal(requested_target_duration_ms)
                * (Decimal("1") + self.maximum_relative_extension_ratio)
            ).to_integral_value(rounding=ROUND_FLOOR)
        )
        return min(absolute_limit, relative_limit)


@dataclass(frozen=True, slots=True)
class AudioFirstDurationResolution:
    requested_target_duration_ms: int
    resolved_scene_durations_ms: tuple[int, ...]
    resolved_duration_ms: int
    maximum_allowed_duration_ms: int


class ResolvedSceneDuration(ContractModel):
    scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    sequence_index: int = Field(ge=0, le=49)
    planned_duration_ms: int = Field(gt=0, le=600_000)
    actual_narration_duration_ms: int = Field(ge=0, le=600_000)
    resolved_duration_ms: int = Field(gt=0, le=600_000)

    @model_validator(mode="after")
    def validate_resolution(self) -> ResolvedSceneDuration:
        if self.resolved_duration_ms != max(
            self.planned_duration_ms,
            self.actual_narration_duration_ms,
        ):
            raise ValueError("resolved scene duration differs from audio-first policy")
        return self


class DurableDurationResolution(ContractModel):
    schema_version: str = "1.0.0"
    requested_target_duration_ms: int = Field(gt=0, le=3_600_000)
    scenes: tuple[ResolvedSceneDuration, ...] = Field(min_length=1, max_length=50)
    resolved_duration_ms: int = Field(gt=0, le=3_600_000)
    maximum_allowed_duration_ms: int = Field(gt=0, le=3_600_000)
    accepted: bool

    @model_validator(mode="after")
    def validate_record(self) -> DurableDurationResolution:
        indexes = tuple(scene.sequence_index for scene in self.scenes)
        if indexes != tuple(range(len(self.scenes))):
            raise ValueError("resolved scenes must use deterministic ordering")
        if len({scene.scene_id for scene in self.scenes}) != len(self.scenes):
            raise ValueError("resolved scene identities must be unique")
        if sum(scene.planned_duration_ms for scene in self.scenes) != (
            self.requested_target_duration_ms
        ):
            raise ValueError("planned scene durations differ from requested target")
        if sum(scene.resolved_duration_ms for scene in self.scenes) != self.resolved_duration_ms:
            raise ValueError("resolved scene durations differ from resolved total")
        if self.accepted != (self.resolved_duration_ms <= self.maximum_allowed_duration_ms):
            raise ValueError("duration resolution acceptance differs from tolerance")
        return self


def durable_duration_resolution(
    *,
    scene_ids: tuple[str, ...],
    planned_scene_durations_ms: tuple[int, ...],
    narration_scene_durations_ms: tuple[int, ...],
    resolution: AudioFirstDurationResolution,
) -> DurableDurationResolution:
    if not (
        len(scene_ids)
        == len(planned_scene_durations_ms)
        == len(narration_scene_durations_ms)
        == len(resolution.resolved_scene_durations_ms)
    ):
        raise ValueError("duration resolution inputs must align")
    return DurableDurationResolution(
        requested_target_duration_ms=resolution.requested_target_duration_ms,
        scenes=tuple(
            ResolvedSceneDuration(
                scene_id=scene_id,
                sequence_index=index,
                planned_duration_ms=planned,
                actual_narration_duration_ms=narration,
                resolved_duration_ms=resolved,
            )
            for index, (scene_id, planned, narration, resolved) in enumerate(
                zip(
                    scene_ids,
                    planned_scene_durations_ms,
                    narration_scene_durations_ms,
                    resolution.resolved_scene_durations_ms,
                    strict=True,
                )
            )
        ),
        resolved_duration_ms=resolution.resolved_duration_ms,
        maximum_allowed_duration_ms=resolution.maximum_allowed_duration_ms,
        accepted=resolution.resolved_duration_ms <= resolution.maximum_allowed_duration_ms,
    )


def resolve_audio_first_durations(
    *,
    requested_target_duration_ms: int,
    planned_scene_durations_ms: tuple[int, ...],
    narration_scene_durations_ms: tuple[int, ...],
    policy: DurationResolutionPolicy,
) -> AudioFirstDurationResolution:
    """Resolve natural scene durations and reject excessive extension before billing."""

    if not planned_scene_durations_ms:
        raise ValueError("audio-first duration resolution requires scenes")
    if len(planned_scene_durations_ms) != len(narration_scene_durations_ms):
        raise ValueError("planned and narration scene durations must align")
    if any(value <= 0 for value in planned_scene_durations_ms):
        raise ValueError("planned scene durations must be positive")
    if any(value < 0 for value in narration_scene_durations_ms):
        raise ValueError("narration scene durations cannot be negative")
    if sum(planned_scene_durations_ms) != requested_target_duration_ms:
        raise ValueError("planned scene durations must equal the requested target")
    resolved = tuple(
        max(planned, narration)
        for planned, narration in zip(
            planned_scene_durations_ms,
            narration_scene_durations_ms,
            strict=True,
        )
    )
    resolved_total = sum(resolved)
    maximum_allowed = policy.maximum_allowed_duration_ms(requested_target_duration_ms)
    resolution = AudioFirstDurationResolution(
        requested_target_duration_ms=requested_target_duration_ms,
        resolved_scene_durations_ms=resolved,
        resolved_duration_ms=resolved_total,
        maximum_allowed_duration_ms=maximum_allowed,
    )
    if resolved_total > maximum_allowed:
        raise DurationResolutionError(
            "resolved narration duration exceeds the configured target tolerance",
            resolution=resolution,
        )
    return resolution


__all__ = [
    "AudioFirstDurationResolution",
    "DurableDurationResolution",
    "DurationResolutionError",
    "DurationResolutionPolicy",
    "ResolvedSceneDuration",
    "durable_duration_resolution",
    "resolve_audio_first_durations",
]
