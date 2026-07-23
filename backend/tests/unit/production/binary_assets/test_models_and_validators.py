"""Contract and standalone validator coverage."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.src.production.binary_assets.configuration import (
    AssetStorageConfiguration,
)
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetConfigurationError,
    BinaryAssetCorruptError,
    BinaryAssetHashError,
    BinaryAssetMimeError,
    BinaryAssetSizeError,
)
from backend.src.production.binary_assets.models import (
    BinaryAssetRole,
    ProductionBinaryAsset,
    ProductionBinaryAssetMetadata,
)
from backend.src.production.binary_assets.validators import (
    AssetHashValidator,
    AssetMimeValidator,
    AssetSizeValidator,
)
from backend.tests.unit.production.binary_assets.conftest import image_bytes

JOB_ID = UUID("11111111-1111-4111-8111-111111111111")


def valid_asset(**overrides) -> ProductionBinaryAsset:
    values = {
        "asset_id": "asset-s001-q001-v001",
        "job_id": JOB_ID,
        "scene_id": "scene-001",
        "shot_id": "scene-001-shot-001",
        "asset_role": BinaryAssetRole.PRIMARY,
        "mime_type": "image/png",
        "extension": "png",
        "sha256": "a" * 64,
        "size_bytes": 10,
        "width": 4,
        "height": 3,
        "created_at": datetime(2026, 7, 23, tzinfo=UTC),
        "storage_path": (
            f"production/{JOB_ID}/assets/images/asset-s001-q001-v001.png"
        ),
    }
    values.update(overrides)
    return ProductionBinaryAsset(**values)


def test_binary_asset_contract_is_frozen_and_forbids_extra() -> None:
    asset = valid_asset()
    with pytest.raises(ValidationError):
        asset.width = 20
    with pytest.raises(ValidationError):
        ProductionBinaryAsset(**asset.model_dump(), unexpected=True)


def test_contract_rejects_wrong_scene_and_non_contractual_path() -> None:
    with pytest.raises(ValidationError):
        valid_asset(shot_id="scene-002-shot-001")
    with pytest.raises(ValidationError):
        valid_asset(storage_path="../escape.png")


@pytest.mark.parametrize(
    "attributes",
    [
        {"api_key": "secret"},
        {"authorization_token": "secret"},
        {"path": "C:\\outside\\asset.png"},
    ],
)
def test_metadata_rejects_secrets_and_absolute_paths(attributes) -> None:
    with pytest.raises((ValidationError, ValueError)):
        ProductionBinaryAssetMetadata(attributes=attributes)


def test_token_metrics_are_safe_metadata() -> None:
    metadata = ProductionBinaryAssetMetadata(
        attributes={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
    )
    assert metadata.attributes["total_tokens"] == 30


def test_storage_configuration_is_strict(tmp_path) -> None:
    with pytest.raises(BinaryAssetConfigurationError):
        AssetStorageConfiguration(workspace=tmp_path, max_asset_size=0)
    with pytest.raises(BinaryAssetConfigurationError):
        AssetStorageConfiguration(
            workspace=tmp_path,
            allowed_mime_types=frozenset({"application/octet-stream"}),
        )
    with pytest.raises(BinaryAssetConfigurationError):
        AssetStorageConfiguration(
            workspace=tmp_path,
            allowed_extensions=frozenset({"exe"}),
        )


def test_mime_validator_detects_actual_image(
    storage_configuration,
    png_bytes,
) -> None:
    inspected = AssetMimeValidator(storage_configuration).validate(
        png_bytes,
        declared_mime_type="image/png",
        extension="png",
    )
    assert (inspected.width, inspected.height, inspected.mime_type) == (
        4,
        3,
        "image/png",
    )


@pytest.mark.parametrize(
    ("image_format", "mime_type", "extension"),
    [
        ("PNG", "image/png", "png"),
        ("JPEG", "image/jpeg", "jpg"),
        ("WEBP", "image/webp", "webp"),
    ],
)
def test_all_configured_image_formats_are_verified(
    storage_configuration,
    image_format,
    mime_type,
    extension,
) -> None:
    inspected = AssetMimeValidator(storage_configuration).validate(
        image_bytes(image_format=image_format),
        declared_mime_type=mime_type,
        extension=extension,
    )
    assert inspected.mime_type == mime_type


def test_mime_validator_rejects_mismatch_and_corruption(
    storage_configuration,
    png_bytes,
) -> None:
    validator = AssetMimeValidator(storage_configuration)
    with pytest.raises(BinaryAssetMimeError):
        validator.validate(
            png_bytes,
            declared_mime_type="image/jpeg",
            extension="jpg",
        )
    with pytest.raises(BinaryAssetCorruptError):
        validator.validate(
            b"not-an-image",
            declared_mime_type="image/png",
            extension="png",
        )


def test_hash_and_size_validators(storage_configuration, png_bytes) -> None:
    hash_validator = AssetHashValidator()
    digest = hash_validator.calculate(png_bytes)
    assert hash_validator.validate(png_bytes, expected_sha256=digest) == digest
    with pytest.raises(BinaryAssetHashError):
        hash_validator.validate(png_bytes, expected_sha256="0" * 64)
    size_validator = AssetSizeValidator(storage_configuration)
    assert size_validator.validate(png_bytes) == len(png_bytes)
    with pytest.raises(BinaryAssetSizeError):
        size_validator.validate(png_bytes, expected_size=len(png_bytes) + 1)


def test_size_limit_is_enforced(tmp_path, png_bytes) -> None:
    configuration = AssetStorageConfiguration(
        workspace=tmp_path,
        max_asset_size=len(png_bytes) - 1,
    )
    with pytest.raises(BinaryAssetSizeError):
        AssetSizeValidator(configuration).validate(png_bytes)
