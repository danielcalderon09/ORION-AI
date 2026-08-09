"""Typed failures for durable video clip generation."""
# ruff: noqa: N818


class VideoClipGenerationError(RuntimeError):
    """Base video clip generation failure."""


class VideoClipValidationError(VideoClipGenerationError):
    pass


class VideoClipUnsupportedInputException(VideoClipValidationError):
    pass


class VideoClipProviderError(VideoClipGenerationError):
    pass


class VideoClipProviderDependencyException(VideoClipProviderError):
    pass


class VideoClipProviderTimeoutException(VideoClipProviderError):
    pass


class VideoClipProviderResponseException(VideoClipProviderError):
    pass


class ImageAcquisitionManifestReadError(VideoClipGenerationError):
    pass


class ImageAcquisitionManifestNotFoundException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestAmbiguousException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestTypeException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestJobException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestPathException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestLinkException(ImageAcquisitionManifestPathException):
    pass


class ImageAcquisitionManifestMissingFileException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestSizeException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestChecksumException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestEncodingException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestJsonException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestSchemaException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestVersionException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestIncompleteException(ImageAcquisitionManifestReadError):
    pass


class SourceImageMissingException(ImageAcquisitionManifestReadError):
    pass


class SourceImageCorruptException(ImageAcquisitionManifestReadError):
    pass


class SourceImageProvenanceException(ImageAcquisitionManifestReadError):
    pass


class ImageAcquisitionManifestTransientReadException(ImageAcquisitionManifestReadError):
    pass


class VideoClipStoreError(VideoClipGenerationError):
    pass


class VideoClipNotFoundError(VideoClipStoreError):
    pass


class VideoClipConflictError(VideoClipStoreError):
    pass


class VideoClipIntegrityError(VideoClipStoreError):
    pass


class VideoClipPathError(VideoClipStoreError):
    pass


class VideoClipLinkError(VideoClipPathError):
    pass


class VideoClipManifestError(VideoClipGenerationError):
    pass


class VideoClipManifestConflictException(VideoClipManifestError):
    pass


class VideoClipManifestCorruptException(VideoClipManifestError):
    pass


class OpenRouterVideoError(VideoClipProviderError):
    """Base typed OpenRouter video failure."""

    http_status: int | None = None
    diagnostic_phase: str | None = None
    diagnostic_code: str | None = None
    diagnostic_metadata: dict[str, object]

    def __init__(
        self,
        message: str,
        *,
        diagnostic_phase: str | None = None,
        diagnostic_code: str | None = None,
        diagnostic_metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic_phase = diagnostic_phase
        self.diagnostic_code = diagnostic_code
        self.diagnostic_metadata = dict(diagnostic_metadata or {})

    def add_diagnostic(
        self,
        *,
        phase: str | None = None,
        code: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Add adapter-owned safe context without replacing more specific context."""

        if self.diagnostic_phase is None:
            self.diagnostic_phase = phase
        if self.diagnostic_code is None:
            self.diagnostic_code = code
        if metadata:
            for key, value in metadata.items():
                self.diagnostic_metadata.setdefault(key, value)


class OpenRouterVideoConfigurationError(OpenRouterVideoError):
    pass


class OpenRouterVideoAuthenticationError(OpenRouterVideoError):
    pass


class OpenRouterVideoPermissionError(OpenRouterVideoError):
    pass


class OpenRouterVideoInsufficientCreditsError(OpenRouterVideoError):
    pass


class OpenRouterVideoInvalidRequestError(OpenRouterVideoError):
    pass


class OpenRouterVideoUnsupportedModelError(OpenRouterVideoError):
    pass


class OpenRouterVideoCapabilityError(OpenRouterVideoError):
    pass


class OpenRouterVideoRateLimitError(OpenRouterVideoError):
    pass


class OpenRouterVideoServerError(OpenRouterVideoError):
    pass


class OpenRouterVideoTransportError(OpenRouterVideoError):
    pass


class OpenRouterVideoTimeoutError(
    OpenRouterVideoError, VideoClipProviderTimeoutException
):
    pass


class OpenRouterVideoInvalidResponseError(OpenRouterVideoError):
    pass


class OpenRouterVideoResponseTooLargeError(OpenRouterVideoError):
    pass


class OpenRouterVideoDownloadError(OpenRouterVideoTransportError):
    pass


class OpenRouterVideoContentTypeError(OpenRouterVideoError):
    pass


class OpenRouterVideoRemoteFailedError(OpenRouterVideoError):
    pass


class OpenRouterVideoRemoteCancelledError(OpenRouterVideoError):
    pass


class OpenRouterVideoRemoteExpiredError(OpenRouterVideoError):
    pass


class OpenRouterVideoUncertainSubmissionError(OpenRouterVideoError):
    pass


class OpenRouterVideoCostPolicyError(OpenRouterVideoError):
    pass


class VideoFramePublicationUnavailableError(OpenRouterVideoError):
    pass


class RemoteVideoJobStoreError(OpenRouterVideoError):
    pass


class RemoteVideoJobConflictError(RemoteVideoJobStoreError):
    pass
