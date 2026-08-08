"""Typed failures for durable image acquisition."""
# ruff: noqa: N818

from decimal import Decimal

from backend.src.production.image_acquisition.diagnostics import (
    ImageDiagnosticMetadata,
    ImageDiagnosticSubtype,
)


class ImageAcquisitionError(RuntimeError):
    """Base image acquisition failure."""


class ImageAcquisitionValidationError(ImageAcquisitionError):
    """Provider-independent contract or mapping is invalid."""


class ImageAcquisitionUnsupportedAssetException(ImageAcquisitionValidationError):
    """The durable visual plan requests a capability not implemented yet."""


class ImageAcquisitionProviderError(ImageAcquisitionError):
    """Base provider failure."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        provider_request_id: str | None = None,
        diagnostic_subtype: ImageDiagnosticSubtype | None = None,
        diagnostic_metadata: ImageDiagnosticMetadata | None = None,
        requested_model: str | None = None,
        reported_model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: float | None = None,
        finish_reason: str | None = None,
        validation_error_code: str | None = None,
        validation_error_path: str | None = None,
        validation_error_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.provider_request_id = provider_request_id
        self.diagnostic_subtype = diagnostic_subtype
        self.diagnostic_metadata = diagnostic_metadata
        self.requested_model = requested_model
        self.reported_model = reported_model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.finish_reason = finish_reason
        self.validation_error_code = validation_error_code
        self.validation_error_path = validation_error_path
        self.validation_error_message = validation_error_message


class ImageAcquisitionProviderDependencyException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderConfigurationException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderAuthenticationException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderRateLimitException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderTimeoutException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderUnavailableException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderUncertainException(ImageAcquisitionProviderError):
    """A paid submission may have been transmitted and must not be repeated."""


class ImageAcquisitionProviderResponseException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderContractException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderPolicyException(ImageAcquisitionProviderError):
    pass


class ImageAcquisitionProviderModelException(ImageAcquisitionProviderError):
    pass


class ProductionVisualAssetPlanReadError(ImageAcquisitionError):
    pass


class ProductionVisualAssetPlanNotFoundException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanAmbiguousException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanTypeException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanJobException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanPathException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanLinkException(ProductionVisualAssetPlanPathException):
    pass


class ProductionVisualAssetPlanMissingFileException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanSizeException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanChecksumException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanEncodingException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanJsonException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanContractException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanVersionException(ProductionVisualAssetPlanReadError):
    pass


class ProductionVisualAssetPlanTransientReadException(ProductionVisualAssetPlanReadError):
    pass


class ImageAcquisitionManifestError(ImageAcquisitionError):
    pass


class ImageAcquisitionManifestConflictException(ImageAcquisitionManifestError):
    pass


class ImageAcquisitionManifestCorruptException(ImageAcquisitionManifestError):
    pass
