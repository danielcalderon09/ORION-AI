"""Fixtures for provider-neutral binary image storage."""

from datetime import UTC, datetime
from io import BytesIO

import pytest
from PIL import Image

from backend.src.production.binary_assets.configuration import (
    AssetStorageConfiguration,
)
from backend.src.production.binary_assets.filesystem_store import (
    FilesystemBinaryAssetStore,
)
from backend.src.production.binary_assets.validators import (
    AssetHashValidator,
    AssetMimeValidator,
    AssetSizeValidator,
    BinaryAssetIntegrityValidator,
)


def image_bytes(*, image_format: str = "PNG", size: tuple[int, int] = (4, 3)) -> bytes:
    stream = BytesIO()
    Image.new("RGB", size, color=(12, 34, 56)).save(stream, format=image_format)
    return stream.getvalue()


@pytest.fixture
def png_bytes() -> bytes:
    return image_bytes()


@pytest.fixture
def storage_configuration(tmp_path):
    return AssetStorageConfiguration(workspace=tmp_path, max_asset_size=1_000_000)


@pytest.fixture
def integrity_validator(storage_configuration):
    return BinaryAssetIntegrityValidator(
        mime_validator=AssetMimeValidator(storage_configuration),
        hash_validator=AssetHashValidator(),
        size_validator=AssetSizeValidator(storage_configuration),
    )


@pytest.fixture
def binary_store(storage_configuration, integrity_validator):
    return FilesystemBinaryAssetStore(
        configuration=storage_configuration,
        integrity_validator=integrity_validator,
        clock=lambda: datetime(2026, 7, 23, 18, 0, tzinfo=UTC),
    )
