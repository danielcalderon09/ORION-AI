"""Deterministic offline visual asset planning provider."""

from backend.src.production.visual_asset_planning.models import (
    AssetKind,
    ContinuityEntityKind,
    GenerationMode,
    ProductionVisualAssetPlan,
    ProductionVisualAssetSpec,
    SeedPolicy,
    VisualAssetRole,
    VisualComposition,
    VisualConsistencyProfile,
    VisualContinuityEntity,
    validate_visual_asset_plan_against_scene_plan,
)
from backend.src.production.visual_asset_planning.ports import (
    VisualAssetPlanningProviderRequest,
    VisualAssetPlanningProviderResponse,
)


class SimulatedVisualAssetPlanningProvider:
    async def generate_visual_asset_plan(
        self,
        request: VisualAssetPlanningProviderRequest,
    ) -> VisualAssetPlanningProviderResponse:
        config = request.configuration
        source = request.scene_plan
        kind = config.preferred_asset_kind
        generation_mode = _generation_mode(kind)
        entities = tuple(
            VisualContinuityEntity(
                entity_id=f"location_{scene.scene_number:02d}",
                kind=ContinuityEntityKind.LOCATION,
                description=f"Approved visual environment for {scene.title}",
            )
            for scene in source.scenes
        )
        assets: list[ProductionVisualAssetSpec] = []
        previous_by_scene: dict[int, str] = {}
        for scene in source.scenes:
            for shot in scene.shots:
                for variant in range(1, config.images_per_shot + 1):
                    asset_id = (
                        f"asset-s{scene.scene_number:03d}-q{shot.shot_number:03d}-v{variant:03d}"
                    )
                    references: tuple[str, ...] = ()
                    if config.allow_reference_assets and scene.scene_number in previous_by_scene:
                        references = (previous_by_scene[scene.scene_number],)
                    role = VisualAssetRole.PRIMARY if variant == 1 else VisualAssetRole.SUPPORTING
                    detail = (
                        f"{shot.description}. Preserve {shot.camera.framing} framing, "
                        f"{shot.camera.angle} angle, {shot.camera.movement} movement, "
                        f"and {shot.camera.lens_millimeters}mm lens intent."
                    )
                    assets.append(
                        ProductionVisualAssetSpec(
                            asset_id=asset_id,
                            scene_number=scene.scene_number,
                            source_scene_id=scene.scene_id,
                            shot_number=shot.shot_number,
                            source_shot_id=shot.shot_id,
                            role=role,
                            asset_kind=kind,
                            generation_mode=generation_mode,
                            prompt=detail,
                            negative_prompt=(
                                "Unsafe content, broken continuity, camera mismatch"
                                if config.negative_prompt_enabled
                                else None
                            ),
                            visual_subject=shot.camera.subject,
                            environment=scene.objective,
                            composition=VisualComposition(
                                layout=f"{shot.camera.framing} composition",
                                focal_point=shot.camera.subject,
                                depth="Layered foreground, subject, and background",
                                action=shot.objective,
                            ),
                            camera_intent=shot.camera,
                            lighting="Consistent motivated cinematic lighting",
                            color_direction="Coherent restrained palette",
                            style_direction="Production-safe cinematic realism",
                            continuity_group=f"location_{scene.scene_number:02d}",
                            reference_asset_ids=references,
                            width=config.target_width,
                            height=config.target_height,
                            aspect_ratio=config.aspect_ratio,
                            expected_duration_seconds=shot.timing.duration_seconds,
                            seed_policy=SeedPolicy.DETERMINISTIC,
                            safety_notes=(
                                ("Safe content only",) if config.safe_content_only else ()
                            ),
                            metadata={"variant": variant},
                        )
                    )
                    previous_by_scene[scene.scene_number] = asset_id
        plan = ProductionVisualAssetPlan(
            source_scene_plan_schema_version=source.schema_version,
            title=source.title,
            language=source.language,
            aspect_ratio=config.aspect_ratio,
            global_visual_direction=(
                "Preserve the approved scene, camera, timing, and deterministic continuity."
            ),
            global_negative_prompt=(
                "Unsafe content, active scripts, text artifacts, inconsistent identities"
                if config.negative_prompt_enabled
                else None
            ),
            consistency_profile=VisualConsistencyProfile(
                entities=entities,
                palette=("balanced neutrals", "controlled accents"),
                lighting_direction="Motivated light remains continuous within each scene",
                style_direction="Consistent cinematic production design",
                period="Contemporary unless the approved scene states otherwise",
                visual_identity="ORION deterministic simulated visual bible",
                continuity_rules=(
                    "Keep location identity stable across shots in the same scene",
                    "Preserve approved camera intent and timing",
                ),
            ),
            assets=tuple(assets),
            metadata={
                "deterministic": True,
                "simulated": True,
                "continuity_strength": config.continuity_strength,
            },
        )
        validate_visual_asset_plan_against_scene_plan(plan, source)
        return VisualAssetPlanningProviderResponse(
            visual_asset_plan=plan,
            provider="orion-simulated",
            model="visual-asset-planning-simulator-v1",
            requested_model="visual-asset-planning-simulator-v1",
            reported_model="visual-asset-planning-simulator-v1",
            latency_ms=0,
            finish_reason="simulated",
            metadata={"deterministic": True, "simulated": True},
        )

    async def close(self) -> None:
        return None


def _generation_mode(kind: AssetKind) -> GenerationMode:
    if kind is AssetKind.VIDEO_CLIP:
        return GenerationMode.TEXT_TO_VIDEO
    if kind is AssetKind.REFERENCE_IMAGE:
        return GenerationMode.SUPPLIED_ASSET
    return GenerationMode.TEXT_TO_IMAGE
