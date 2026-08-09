"""Deterministic derivation of scene prompts from the visual identity."""

from __future__ import annotations

from backend.src.production.visual_asset_planning.models import (
    ProductionVisualAssetSpec,
    VideoIdentity,
)


def render_identity_constraints(identity: VideoIdentity) -> str:
    """Render only stable identity fields in a fixed order."""

    sections = (
        f"Subject: {identity.subject}." if identity.subject else "",
        f"Topic: {identity.topic}." if identity.topic else "",
        f"Premise: {identity.narrative_premise}." if identity.narrative_premise else "",
        f"Visual style: {identity.visual_style}.",
        f"Color palette: {', '.join(identity.color_palette)}." if identity.color_palette else "",
        f"Lighting: {identity.lighting}." if identity.lighting else "",
        f"Environment continuity: {identity.environment}." if identity.environment else "",
        f"Camera language: {identity.camera_language}." if identity.camera_language else "",
        f"Realism/production design: {identity.realism_level}."
        if identity.realism_level
        else "",
        f"Period: {identity.period}." if identity.period else "",
        f"Location continuity: {identity.location_continuity}."
        if identity.location_continuity
        else "",
        f"Mood: {identity.mood}." if identity.mood else "",
        (
            "Recurring characters: "
            + "; ".join(
                f"{item.character_id} ({item.role or 'character'}): {item.appearance}"
                + (f"; wardrobe: {item.wardrobe}" if item.wardrobe else "")
                + (
                    f"; continuity: {', '.join(item.continuity_notes)}"
                    if item.continuity_notes
                    else ""
                )
                for item in identity.recurring_characters
            )
            + "."
            if identity.recurring_characters
            else ""
        ),
        f"Recurring objects: {', '.join(identity.recurring_objects)}."
        if identity.recurring_objects
        else "",
        (
            "Visual restrictions: " + "; ".join(identity.visual_restrictions) + "."
            if identity.visual_restrictions
            else ""
        ),
        (
            "Negative constraints: " + "; ".join(identity.negative_constraints) + "."
            if identity.negative_constraints
            else ""
        ),
    )
    return " ".join(section for section in sections if section)


def build_scene_visual_prompt(
    video_identity: VideoIdentity,
    scene_intent: ProductionVisualAssetSpec,
) -> str:
    """Build a stable prompt with explicit continuity/content boundaries."""

    composition = scene_intent.composition
    camera = scene_intent.camera_intent
    continuity = render_identity_constraints(video_identity)
    scene_content = " ".join(
        (
            f"Subject: {scene_intent.visual_subject}.",
            f"Environment: {scene_intent.environment}.",
            f"Action: {composition.action}.",
            f"Composition: {composition.layout}; focal point {composition.focal_point}; "
            f"depth {composition.depth}.",
            f"Camera intent: {camera.framing}, {camera.angle}, {camera.movement}, "
            f"{camera.lens_millimeters}mm, subject {camera.subject}.",
            f"Scene instruction: {scene_intent.prompt}.",
        )
    )
    negative = scene_intent.negative_prompt or "no additional scene-specific negative instruction"
    return (
        "CONTINUITY CONSTRAINTS: "
        + continuity
        + "\nSCENE-SPECIFIC CONTENT: "
        + scene_content
        + f"\nSCENE-SPECIFIC NEGATIVE CONSTRAINTS: {negative}."
    )
