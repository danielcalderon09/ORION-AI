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
    VideoClipProviderTimeoutException, OpenRouterVideoError
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
