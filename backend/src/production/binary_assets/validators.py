"""Composable integrity validators for binary image assets."""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from backend.src.production.binary_assets.configuration import (
    AssetStorageConfiguration,
)
from backend.src.production.binary_assets.exceptions import (
    BinaryAssetCorruptError,
    BinaryAssetHashError,
    BinaryAssetMimeError,
    BinaryAssetSizeError,
)

_FORMAT_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}
_MIME_EXTENSIONS = {
    "image/png": frozenset({"png"}),
    "image/jpeg": frozenset({"jpg", "jpeg"}),
    "image/webp": frozenset({"webp"}),
}


@dataclass(frozen=True, slots=True)
class InspectedImage:
    mime_type: str
    width: int
    height: int


class AssetMimeValidator:
    def __init__(self, configuration: AssetStorageConfiguration) -> None:
        self._configuration = configuration

    def validate(
        self,
        content: bytes,
        *,
        declared_mime_type: str,
        extension: str,
    ) -> InspectedImage:
        mime_type = declared_mime_type.strip().lower()
        normalized_extension = extension.strip().lower().removeprefix(".")
        if mime_type not in self._configuration.allowed_mime_types:
            raise BinaryAssetMimeError("binary asset MIME type is not allowed")
        if normalized_extension not in self._configuration.allowed_extensions:
            raise BinaryAssetMimeError("binary asset extension is not allowed")
        if normalized_extension not in _MIME_EXTENSIONS.get(mime_type, frozenset()):
            raise BinaryAssetMimeError(
                "binary asset extension does not match its declared MIME type"
            )
        try:
            with Image.open(BytesIO(content)) as image:
                detected_format = image.format
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
                width, height = image.size
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise BinaryAssetCorruptError(
                "binary asset is not a complete supported image"
            ) from exc
        detected_mime = _FORMAT_MIME.get(detected_format or "")
        if detected_mime is None:
            raise BinaryAssetMimeError("decoded binary asset format is not allowed")
        if detected_mime != mime_type:
            raise BinaryAssetMimeError(
                "decoded binary asset MIME differs from durable metadata"
            )
        if not 1 <= width <= 16_384 or not 1 <= height <= 16_384:
            raise BinaryAssetMimeError("binary asset dimensions are outside safe limits")
        return InspectedImage(mime_type=detected_mime, width=width, height=height)


class AssetHashValidator:
    @staticmethod
    def calculate(content: bytes) -> str:
        import hashlib

        return hashlib.sha256(content).hexdigest()

    def validate(self, content: bytes, *, expected_sha256: str) -> str:
        actual = self.calculate(content)
        if actual != expected_sha256.lower():
            raise BinaryAssetHashError("binary asset checksum differs from metadata")
        return actual


class AssetSizeValidator:
    def __init__(self, configuration: AssetStorageConfiguration) -> None:
        self._maximum = configuration.max_asset_size

    def validate(self, content: bytes, *, expected_size: int | None = None) -> int:
        size = len(content)
        if size < 1:
            raise BinaryAssetSizeError("binary asset must not be empty")
        if size > self._maximum:
            raise BinaryAssetSizeError("binary asset exceeds the configured size limit")
        if expected_size is not None and size != expected_size:
            raise BinaryAssetSizeError("binary asset size differs from metadata")
        return size


class BinaryAssetIntegrityValidator:
    """Validate bytes without knowing storage, persistence, or providers."""

    def __init__(
        self,
        *,
        mime_validator: AssetMimeValidator,
        hash_validator: AssetHashValidator,
        size_validator: AssetSizeValidator,
    ) -> None:
        self._mime = mime_validator
        self._hash = hash_validator
        self._size = size_validator

    def validate_new(
        self,
        content: bytes,
        *,
        mime_type: str,
        extension: str,
    ) -> tuple[str, int, InspectedImage]:
        size = self._size.validate(content)
        inspected = self._mime.validate(
            content,
            declared_mime_type=mime_type,
            extension=extension,
        )
        return self._hash.calculate(content), size, inspected

    def validate_existing(
        self,
        content: bytes,
        *,
        mime_type: str,
        extension: str,
        sha256: str,
        size_bytes: int,
        width: int,
        height: int,
    ) -> InspectedImage:
        self._size.validate(content, expected_size=size_bytes)
        self._hash.validate(content, expected_sha256=sha256)
        inspected = self._mime.validate(
            content,
            declared_mime_type=mime_type,
            extension=extension,
        )
        if inspected.width != width or inspected.height != height:
            raise BinaryAssetMimeError(
                "decoded binary asset dimensions differ from metadata"
            )
        return inspected
