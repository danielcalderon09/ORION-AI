"""Strict provider-independent visual asset planning contracts."""

from __future__ import annotations

import math
import re
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.src.production.application.sanitization import validate_safe_json
from backend.src.production.domain.base import ContractModel
from backend.src.production.planning.validation import validate_planning_text
from backend.src.production.scene_planning.models import (
    ProductionCamera,
    ProductionScenePlan,
)

SUPPORTED_VISUAL_ASSET_PLAN_VERSIONS = frozenset({"1.0.0"})
_ASPECT_RATIOS = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}
_ACTIVE_CONTENT = re.compile(
    r"(?i)(?:<\s*(?:script|iframe|object|embed)\b|javascript\s*:|"
    r"\b(?:powershell|cmd\.exe|bash|sh)\s+(?:-[a-z]+\s+)?(?:curl|wget|rm|del)\b)"
)
_UNSAFE_URI = re.compile(r"(?i)\b(?:file|data)\s*:")
_EXECUTABLE_DOWNLOAD = re.compile(
    r"(?i)\b(?:download|fetch)\b.{0,80}\.(?:exe|msi|bat|cmd|ps1|sh|dll)\b"
)
_BASE64_PAYLOAD = re.compile(r"(?i)(?:base64[,;:]|[A-Za-z0-9+/]{256,}={0,2})")


class AssetKind(StrEnum):
    STILL_IMAGE = "still_image"
    VIDEO_CLIP = "video_clip"
    BACKGROUND_PLATE = "background_plate"
    FOREGROUND_ELEMENT = "foreground_element"
    TEXTURE = "texture"
    OVERLAY = "overlay"
    TITLE_CARD = "title_card"
    REFERENCE_IMAGE = "reference_image"


class GenerationMode(StrEnum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    COMPOSITING = "compositing"
    PROCEDURAL = "procedural"
    SUPPLIED_ASSET = "supplied_asset"


class SeedPolicy(StrEnum):
    DETERMINISTIC = "deterministic"
    RANDOM = "random"
    PROVIDER_MANAGED = "provider_managed"
    INHERITED = "inherited"


class VisualAssetRole(StrEnum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    REFERENCE = "reference"


class ContinuityEntityKind(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"
    PROP = "prop"


class VisualContinuityEntity(ContractModel):
    entity_id: str = Field(pattern=r"^(?:character|location|prop)_[0-9]{2}$")
    kind: ContinuityEntityKind
    description: str = Field(min_length=1, max_length=1000)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return validate_visual_instruction(value)

    @model_validator(mode="after")
    def validate_id_kind(self) -> VisualContinuityEntity:
        if not self.entity_id.startswith(f"{self.kind.value}_"):
            raise ValueError("continuity entity ID must match its kind")
        return self


class VisualConsistencyProfile(ContractModel):
    entities: tuple[VisualContinuityEntity, ...] = Field(default=(), max_length=100)
    palette: tuple[str, ...] = Field(default=(), max_length=20)
    lighting_direction: str = Field(min_length=1, max_length=1000)
    style_direction: str = Field(min_length=1, max_length=1000)
    period: str = Field(min_length=1, max_length=300)
    visual_identity: str = Field(min_length=1, max_length=1000)
    continuity_rules: tuple[str, ...] = Field(default=(), max_length=50)

    @field_validator(
        "lighting_direction",
        "style_direction",
        "period",
        "visual_identity",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        return validate_visual_instruction(value)

    @field_validator("palette", "continuity_rules")
    @classmethod
    def validate_collection(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_visual_instruction(item) for item in value)

    @model_validator(mode="after")
    def validate_entities(self) -> VisualConsistencyProfile:
        identifiers = tuple(entity.entity_id for entity in self.entities)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("continuity entity IDs must be unique")
        return self


class RecurringCharacter(ContractModel):
    """Stable, prompt-safe identity contract for a recurring character."""

    character_id: str = Field(pattern=r"^character_[0-9]{2}$")
    role: str | None = Field(default=None, max_length=300)
    appearance: str = Field(min_length=1, max_length=1200)
    wardrobe: str | None = Field(default=None, max_length=600)
    continuity_notes: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("role", "appearance", "wardrobe")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return None if value is None else validate_visual_instruction(value)

    @field_validator("continuity_notes")
    @classmethod
    def validate_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_visual_instruction(item) for item in value)


class VideoIdentity(ContractModel):
    """The durable visual/story bible shared by every scene prompt."""

    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    subject: str | None = Field(default=None, max_length=1000)
    topic: str | None = Field(default=None, max_length=500)
    narrative_premise: str | None = Field(default=None, max_length=1500)
    visual_style: str = Field(min_length=1, max_length=1200)
    color_palette: tuple[str, ...] = Field(default=(), max_length=20)
    lighting: str | None = Field(default=None, max_length=1000)
    environment: str | None = Field(default=None, max_length=1000)
    camera_language: str | None = Field(default=None, max_length=1000)
    realism_level: str | None = Field(default=None, max_length=300)
    period: str | None = Field(default=None, max_length=300)
    recurring_characters: tuple[RecurringCharacter, ...] = Field(default=(), max_length=50)
    recurring_objects: tuple[str, ...] = Field(default=(), max_length=50)
    location_continuity: str | None = Field(default=None, max_length=1000)
    mood: str | None = Field(default=None, max_length=500)
    visual_restrictions: tuple[str, ...] = Field(default=(), max_length=50)
    negative_constraints: tuple[str, ...] = Field(default=(), max_length=50)

    @field_validator(
        "subject",
        "topic",
        "narrative_premise",
        "visual_style",
        "lighting",
        "environment",
        "camera_language",
        "realism_level",
        "period",
        "location_continuity",
        "mood",
    )
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return None if value is None else validate_visual_instruction(value)

    @field_validator("color_palette", "recurring_objects", "visual_restrictions", "negative_constraints")
    @classmethod
    def validate_collections(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_visual_instruction(item) for item in value)

    @model_validator(mode="after")
    def validate_characters(self) -> VideoIdentity:
        identifiers = tuple(item.character_id for item in self.recurring_characters)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("recurring character IDs must be unique")
        return self


class VisualComposition(ContractModel):
    layout: str = Field(min_length=1, max_length=1000)
    focal_point: str = Field(min_length=1, max_length=500)
    depth: str = Field(min_length=1, max_length=500)
    action: str = Field(min_length=1, max_length=1000)

    @field_validator("layout", "focal_point", "depth", "action")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return validate_visual_instruction(value)


class ProductionVisualAssetSpec(ContractModel):
    asset_id: str = Field(pattern=r"^asset-s[0-9]{3}-q[0-9]{3}-v[0-9]{3}$")
    scene_number: int = Field(ge=1, le=50)
    source_scene_id: str = Field(pattern=r"^scene-[0-9]{3}$")
    shot_number: int = Field(ge=1, le=100)
    source_shot_id: str = Field(pattern=r"^scene-[0-9]{3}-shot-[0-9]{3}$")
    role: VisualAssetRole
    asset_kind: AssetKind
    generation_mode: GenerationMode
    prompt: str = Field(min_length=1, max_length=8000)
    negative_prompt: str | None = Field(default=None, max_length=3000)
    visual_subject: str = Field(min_length=1, max_length=2000)
    environment: str = Field(min_length=1, max_length=2000)
    composition: VisualComposition
    camera_intent: ProductionCamera
    lighting: str = Field(min_length=1, max_length=1000)
    color_direction: str = Field(min_length=1, max_length=1000)
    style_direction: str = Field(min_length=1, max_length=1000)
    continuity_group: str = Field(pattern=r"^(?:character|location|prop)_[0-9]{2}$")
    reference_asset_ids: tuple[str, ...] = Field(default=(), max_length=20)
    width: int = Field(ge=64, le=8192)
    height: int = Field(ge=64, le=8192)
    aspect_ratio: str = Field(pattern=r"^(?:16:9|9:16|1:1)$")
    expected_duration_seconds: float = Field(gt=0, le=600)
    seed_policy: SeedPolicy
    safety_notes: tuple[str, ...] = Field(default=(), max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "prompt",
        "negative_prompt",
        "visual_subject",
        "environment",
        "lighting",
        "color_direction",
        "style_direction",
    )
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return None if value is None else validate_visual_instruction(value)

    @field_validator("safety_notes")
    @classmethod
    def validate_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_visual_instruction(item) for item in value)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="visual_asset.metadata")
        if not isinstance(validated, dict):
            raise ValueError("visual asset metadata must be an object")
        return validated

    @model_validator(mode="after")
    def validate_mapping_and_dimensions(self) -> ProductionVisualAssetSpec:
        if self.source_scene_id != f"scene-{self.scene_number:03d}":
            raise ValueError("source scene ID must match scene number")
        if self.source_shot_id != (f"scene-{self.scene_number:03d}-shot-{self.shot_number:03d}"):
            raise ValueError("source shot ID must match scene and shot numbers")
        expected_ratio = _ASPECT_RATIOS[self.aspect_ratio]
        actual_ratio = self.width / self.height
        if not math.isclose(actual_ratio, expected_ratio, rel_tol=0.01):
            raise ValueError("asset dimensions do not match aspect ratio")
        if self.asset_id in self.reference_asset_ids:
            raise ValueError("an asset cannot reference itself")
        if len(self.reference_asset_ids) != len(set(self.reference_asset_ids)):
            raise ValueError("reference asset IDs must be unique")
        return self


class ProductionVisualAssetPlan(ContractModel):
    schema_version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    source_scene_plan_schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    source_scene_plan_artifact_id: UUID | None = None
    source_scene_plan_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    title: str = Field(min_length=1, max_length=300)
    language: str = Field(min_length=2, max_length=16)
    aspect_ratio: str = Field(pattern=r"^(?:16:9|9:16|1:1)$")
    global_visual_direction: str = Field(min_length=1, max_length=3000)
    global_negative_prompt: str | None = Field(default=None, max_length=3000)
    consistency_profile: VisualConsistencyProfile
    # Optional keeps pre-identity durable plans readable. New plans receive a
    # derived identity in the handler before they are persisted.
    video_identity: VideoIdentity | None = None
    assets: tuple[ProductionVisualAssetSpec, ...] = Field(
        min_length=1,
        max_length=5000,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "title",
        "language",
        "global_visual_direction",
        "global_negative_prompt",
    )
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return None if value is None else validate_visual_instruction(value)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validated = validate_safe_json(value, path="visual_asset_plan.metadata")
        if not isinstance(validated, dict):
            raise ValueError("visual asset plan metadata must be an object")
        return validated

    @model_validator(mode="after")
    def validate_asset_graph(self) -> ProductionVisualAssetPlan:
        validate_asset_identity_and_order(self.assets)
        validate_asset_references(self.assets)
        entity_ids = {item.entity_id for item in self.consistency_profile.entities}
        if any(asset.continuity_group not in entity_ids for asset in self.assets):
            raise ValueError("asset continuity group is not declared")
        if any(asset.aspect_ratio != self.aspect_ratio for asset in self.assets):
            raise ValueError("every asset must use the plan aspect ratio")
        return self


def derive_video_identity(plan: ProductionVisualAssetPlan) -> VideoIdentity:
    """Derive one deterministic identity from the approved visual plan."""

    entities = plan.consistency_profile.entities
    characters = tuple(
        RecurringCharacter(
            character_id=entity.entity_id,
            role="recurring visual subject",
            appearance=entity.description,
            continuity_notes=plan.consistency_profile.continuity_rules,
        )
        for entity in entities
        if entity.kind is ContinuityEntityKind.CHARACTER
    )
    locations = tuple(
        entity.description
        for entity in entities
        if entity.kind is ContinuityEntityKind.LOCATION
    )
    objects = tuple(
        entity.description
        for entity in entities
        if entity.kind is ContinuityEntityKind.PROP
    )
    negative = (plan.global_negative_prompt,) if plan.global_negative_prompt else ()
    return VideoIdentity(
        # Scene-specific subjects stay in the asset intent; they must not
        # mutate the global identity when one scene is edited.
        subject=plan.title,
        topic=plan.title,
        narrative_premise=plan.global_visual_direction,
        visual_style=plan.consistency_profile.visual_identity,
        color_palette=plan.consistency_profile.palette,
        lighting=plan.consistency_profile.lighting_direction,
        environment=", ".join(locations) if locations else None,
        camera_language="Preserve the approved camera language across scenes",
        realism_level=plan.consistency_profile.style_direction,
        period=plan.consistency_profile.period,
        recurring_characters=characters,
        recurring_objects=objects,
        location_continuity="; ".join(plan.consistency_profile.continuity_rules) or None,
        visual_restrictions=plan.consistency_profile.continuity_rules,
        negative_constraints=negative,
    )


def validate_asset_identity_and_order(
    assets: tuple[ProductionVisualAssetSpec, ...],
) -> None:
    identifiers = tuple(asset.asset_id for asset in assets)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("visual asset IDs must be unique")
    order = tuple((asset.scene_number, asset.shot_number, asset.asset_id) for asset in assets)
    if order != tuple(sorted(order)):
        raise ValueError("visual assets must use deterministic scene/shot/ID order")


def validate_asset_references(
    assets: tuple[ProductionVisualAssetSpec, ...],
) -> None:
    seen: set[str] = set()
    known = {asset.asset_id for asset in assets}
    for asset in assets:
        for reference in asset.reference_asset_ids:
            if reference not in known:
                raise ValueError("reference asset ID does not exist")
            if reference not in seen:
                raise ValueError("reference assets must point to an earlier asset")
        seen.add(asset.asset_id)


def validate_visual_asset_plan_against_scene_plan(
    plan: ProductionVisualAssetPlan,
    scene_plan: ProductionScenePlan,
    *,
    source_scene_plan_artifact_id: UUID | None = None,
    source_scene_plan_sha256: str | None = None,
) -> ProductionVisualAssetPlan:
    """Validate the immutable mapping to the approved durable Scene Plan."""

    if plan.schema_version not in SUPPORTED_VISUAL_ASSET_PLAN_VERSIONS:
        raise ValueError("visual asset plan schema version is unsupported")
    if plan.source_scene_plan_schema_version != scene_plan.schema_version:
        raise ValueError("source scene plan schema version does not match")
    if (
        source_scene_plan_artifact_id is not None
        and plan.source_scene_plan_artifact_id != source_scene_plan_artifact_id
    ):
        raise ValueError("source scene plan artifact ID does not match")
    if (
        source_scene_plan_sha256 is not None
        and plan.source_scene_plan_sha256 != source_scene_plan_sha256
    ):
        raise ValueError("source scene plan checksum does not match")
    if plan.title != scene_plan.title:
        raise ValueError("visual asset plan title does not match scene plan")
    if plan.language.casefold() != scene_plan.language.casefold():
        raise ValueError("visual asset plan language does not match scene plan")

    source_scenes = {scene.scene_number: scene for scene in scene_plan.scenes}
    source_shots = {
        (scene.scene_number, shot.shot_number): (scene, shot)
        for scene in scene_plan.scenes
        for shot in scene.shots
    }
    primary_shots: set[tuple[int, int]] = set()
    for asset in plan.assets:
        scene = source_scenes.get(asset.scene_number)
        pair = source_shots.get((asset.scene_number, asset.shot_number))
        if scene is None or pair is None:
            raise ValueError("visual asset references a missing scene or shot")
        _, shot = pair
        if asset.source_scene_id != scene.scene_id or asset.source_shot_id != shot.shot_id:
            raise ValueError("visual asset source IDs are inconsistent")
        if asset.camera_intent != shot.camera:
            raise ValueError("visual asset must preserve approved camera intent")
        if not math.isclose(
            asset.expected_duration_seconds,
            shot.timing.duration_seconds,
            abs_tol=0.001,
        ):
            raise ValueError("visual asset duration must match approved shot timing")
        if asset.role is VisualAssetRole.PRIMARY:
            primary_shots.add((asset.scene_number, asset.shot_number))
    if primary_shots != set(source_shots):
        raise ValueError("each shot must have at least one primary visual asset")
    return plan


def validate_visual_instruction(value: str) -> str:
    """Validate executable-boundary risks without censoring ordinary creative text."""

    normalized = validate_planning_text(value)
    if _UNSAFE_URI.search(normalized):
        raise ValueError("file and data URLs are not allowed")
    if _ACTIVE_CONTENT.search(normalized):
        raise ValueError("active HTML, JavaScript, or shell content is not allowed")
    if _EXECUTABLE_DOWNLOAD.search(normalized):
        raise ValueError("instructions to download executables are not allowed")
    if _BASE64_PAYLOAD.search(normalized):
        raise ValueError("embedded base64 or binary payloads are not allowed")
    windows = PureWindowsPath(normalized)
    posix = PurePosixPath(normalized)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise ValueError("absolute filesystem paths are not allowed")
    return normalized
