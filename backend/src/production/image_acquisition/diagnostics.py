"""Closed, sanitized diagnostics for remote image failures."""

from enum import StrEnum

from pydantic import Field

from backend.src.production.domain.base import ContractModel


class ImageDiagnosticSubtype(StrEnum):
    PROVIDER_HTTP_ERROR = "provider_http_error"
    PROVIDER_AUTHENTICATION = "provider_authentication"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_POLICY = "provider_policy"
    PROVIDER_MODEL = "provider_model"
    PROVIDER_CONTRACT = "provider_contract"
    PROVIDER_ENVELOPE = "provider_envelope"
    PROVIDER_BODY_ERROR = "provider_body_error"
    MISSING_IMAGE = "missing_image"
    MULTIPLE_IMAGES = "multiple_images"
    INVALID_BASE64 = "invalid_base64"
    DECODED_IMAGE_TOO_LARGE = "decoded_image_too_large"
    MIME_MISMATCH = "mime_mismatch"
    INVALID_IMAGE_SIGNATURE = "invalid_image_signature"
    UNDECODABLE_IMAGE = "undecodable_image"
    UNSUPPORTED_IMAGE_FORMAT = "unsupported_image_format"
    INVALID_DIMENSIONS = "invalid_dimensions"
    ASPECT_RATIO_MISMATCH = "aspect_ratio_mismatch"
    RESPONSE_MODEL_VALIDATION = "response_model_validation"
    BINARY_ASSET_VALIDATION = "binary_asset_validation"
    BINARY_ASSET_WRITE = "binary_asset_write"
    MANIFEST_WRITE = "manifest_write"
    UNCERTAIN_TRANSPORT = "uncertain_transport"
    UNKNOWN_IMAGE_ERROR = "unknown_image_error"


class ImageDiagnosticMetadata(ContractModel):
    """Technical image facts only; never image bytes or provider bodies."""

    declared_media_type: str | None = Field(default=None, max_length=100)
    detected_media_type: str | None = Field(default=None, max_length=100)
    decoded_width: int | None = Field(default=None, gt=0, le=100_000)
    decoded_height: int | None = Field(default=None, gt=0, le=100_000)
    decoded_format: str | None = Field(default=None, max_length=20)
    decoded_size_bytes: int | None = Field(default=None, gt=0, le=250_000_000)
    expected_width: int | None = Field(default=None, gt=0, le=100_000)
    expected_height: int | None = Field(default=None, gt=0, le=100_000)
    expected_aspect_ratio: float | None = Field(default=None, gt=0, le=100)
    actual_aspect_ratio: float | None = Field(default=None, gt=0, le=100)
    requested_output_format: str | None = Field(default=None, max_length=20)


__all__ = ["ImageDiagnosticMetadata", "ImageDiagnosticSubtype"]
