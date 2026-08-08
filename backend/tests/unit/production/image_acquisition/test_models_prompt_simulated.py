"""Provider-neutral contracts, prompts, and simulated raster tests."""

import json
from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

from backend.src.production.image_acquisition.configuration import (
    ImageAcquisitionConfiguration,
)
from backend.src.production.image_acquisition.exceptions import (
    ImageAcquisitionValidationError,
)
from backend.src.production.image_acquisition.models import (
    ImageAcquisitionEntryStatus,
    ImageAcquisitionManifestStatus,
    ProductionImageAcquisitionEntry,
    ProductionImageAcquisitionManifest,
    replace_manifest_entry,
    summarize_entries,
    validate_manifest_transition,
)
from backend.src.production.image_acquisition.ports import (
    ImageAcquisitionProviderRequest,
)
from backend.src.production.image_acquisition.prompt_builder import (
    ImageGenerationPromptBuilder,
)
from backend.src.production.image_acquisition.providers import (
    SimulatedImageAcquisitionProvider,
)
from backend.src.production.image_acquisition.serialization import (
    deserialize_image_acquisition_manifest,
    serialize_image_acquisition_manifest,
)
from backend.tests.unit.production.image_acquisition.conftest import (
    COMMAND_ID,
    JOB_ID,
    VISUAL_PLAN_ARTIFACT_ID,
)


def request(asset, *, output_format="png"):
    return ImageAcquisitionProviderRequest(
        job_id=JOB_ID,
        command_id=COMMAND_ID,
        correlation_id=JOB_ID,
        attempt_number=1,
        visual_asset=asset,
        configuration=ImageAcquisitionConfiguration(
            output_format=output_format,
        ),
    )


def pending_entry(asset) -> ProductionImageAcquisitionEntry:
    return ProductionImageAcquisitionEntry(
        visual_asset_id=asset.asset_id,
        scene_number=asset.scene_number,
        source_scene_id=asset.source_scene_id,
        shot_number=asset.shot_number,
        source_shot_id=asset.source_shot_id,
        role=asset.role,
        generation_mode=asset.generation_mode,
        status=ImageAcquisitionEntryStatus.PENDING,
        attempt_number=1,
    )


def manifest(entry) -> ProductionImageAcquisitionManifest:
    entries = (entry,)
    return ProductionImageAcquisitionManifest(
        source_visual_asset_plan_schema_version="1.0.0",
        source_visual_asset_plan_artifact_id=VISUAL_PLAN_ARTIFACT_ID,
        source_visual_asset_plan_sha256="b" * 64,
        provider="simulated",
        status=ImageAcquisitionManifestStatus.IN_PROGRESS,
        entries=entries,
        summary=summarize_entries(entries),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output_format", "mime_type"),
    [("png", "image/png"), ("jpeg", "image/jpeg"), ("webp", "image/webp")],
)
async def test_simulated_provider_is_deterministic_valid_raster(
    visual_asset_plan,
    output_format,
    mime_type,
) -> None:
    provider = SimulatedImageAcquisitionProvider()
    first = await provider.generate_image(
        request(visual_asset_plan.assets[0], output_format=output_format)
    )
    second = await provider.generate_image(
        request(visual_asset_plan.assets[0], output_format=output_format)
    )
    assert first.images[0].content == second.images[0].content
    assert first.images[0].mime_type == mime_type
    assert "content=" not in repr(first.images[0])
    with Image.open(BytesIO(first.images[0].content)) as image:
        assert image.size == (64, 64)
        image.verify()
    await provider.close()


@pytest.mark.asyncio
async def test_simulated_assets_have_distinct_bytes(visual_asset_plan) -> None:
    provider = SimulatedImageAcquisitionProvider()
    outputs = [
        await provider.generate_image(request(asset))
        for asset in visual_asset_plan.assets
    ]
    assert outputs[0].images[0].content != outputs[1].images[0].content


def test_prompt_is_safe_deterministic_and_bounded(visual_asset_plan) -> None:
    builder = ImageGenerationPromptBuilder(max_prompt_bytes=10_000)
    built = builder.build(request(visual_asset_plan.assets[0]))
    assert built == builder.build(request(visual_asset_plan.assets[0]))
    assert built.size_bytes == len(built.text.encode())
    assert len(built.sha256) == 64
    assert "Authorization" not in built.text
    with pytest.raises(ImageAcquisitionValidationError):
        ImageGenerationPromptBuilder(max_prompt_bytes=1).build(
            request(visual_asset_plan.assets[0])
        )


def test_manifest_is_frozen_strict_and_canonical(visual_asset_plan) -> None:
    value = manifest(pending_entry(visual_asset_plan.assets[0]))
    assert serialize_image_acquisition_manifest(value) == (
        serialize_image_acquisition_manifest(value)
    )
    assert deserialize_image_acquisition_manifest(
        serialize_image_acquisition_manifest(value)
    ) == value
    with pytest.raises(ValidationError):
        ProductionImageAcquisitionManifest.model_validate(
            {**value.model_dump(mode="json"), "unknown": True}
        )
    with pytest.raises(ValidationError):
        value.provider = "changed"  # type: ignore[misc]


def test_historical_manifest_without_diagnostics_still_loads(visual_asset_plan) -> None:
    value = manifest(pending_entry(visual_asset_plan.assets[0]))
    historical = value.model_dump(mode="json", exclude_none=True)

    loaded = deserialize_image_acquisition_manifest(json.dumps(historical).encode())

    assert loaded.entries[0].diagnostic_subtype is None
    assert loaded.entries[0].diagnostic_metadata is None


def test_manifest_transitions_reject_regression(visual_asset_plan) -> None:
    original = manifest(pending_entry(visual_asset_plan.assets[0]))
    generating_entry = original.entries[0].model_copy(
        update={"status": ImageAcquisitionEntryStatus.GENERATING}
    )
    generating = replace_manifest_entry(original, generating_entry)
    validate_manifest_transition(original, generating)
    stored_payload = generating.entries[0].model_dump(mode="python")
    stored_payload.update(
        {
            "status": "stored",
            "binary_asset_id": "image-asset-s001-q001-v001",
            "binary_artifact_id": VISUAL_PLAN_ARTIFACT_ID,
            "storage_path": (
                f"production/{JOB_ID}/assets/images/"
                "image-asset-s001-q001-v001.png"
            ),
            "mime_type": "image/png",
            "extension": "png",
            "sha256": "c" * 64,
            "size_bytes": 100,
            "width": 64,
            "height": 64,
            "provider": "simulated",
        }
    )
    stored = replace_manifest_entry(
        generating,
        ProductionImageAcquisitionEntry.model_validate(stored_payload),
    )
    validate_manifest_transition(generating, stored)
    with pytest.raises(ValueError):
        validate_manifest_transition(stored, original)


def test_completed_manifest_cannot_contain_pending(visual_asset_plan) -> None:
    value = manifest(pending_entry(visual_asset_plan.assets[0]))
    with pytest.raises(ValidationError):
        ProductionImageAcquisitionManifest.model_validate(
            {
                **value.model_dump(mode="python"),
                "status": "completed",
            }
        )


def test_manifest_rejects_duplicate_json_and_unsafe_metadata(
    visual_asset_plan,
) -> None:
    value = manifest(pending_entry(visual_asset_plan.assets[0]))
    content = serialize_image_acquisition_manifest(value)
    with pytest.raises(ValueError):
        deserialize_image_acquisition_manifest(
            content[:-1] + b',"provider":"duplicate"}'
        )
    with pytest.raises(ValidationError):
        ProductionImageAcquisitionManifest.model_validate(
            {
                **value.model_dump(mode="python"),
                "metadata": {"authorization_token": "secret"},
            }
        )
