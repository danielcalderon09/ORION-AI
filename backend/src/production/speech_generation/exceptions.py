"""Typed failures for durable speech generation."""


class SpeechGenerationError(RuntimeError):
    """Base speech-generation failure."""


class SpeechSourceScriptError(SpeechGenerationError):
    pass


class SpeechSourceScriptNotFoundError(SpeechSourceScriptError):
    pass


class SpeechSourceScriptIntegrityError(SpeechSourceScriptError):
    pass


class SpeechSourceScriptChangedError(SpeechSourceScriptIntegrityError):
    pass


class SpeechProviderError(SpeechGenerationError):
    pass


class SpeechProviderClosedError(SpeechProviderError):
    pass


class SpeechProviderResponseError(SpeechProviderError):
    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.provider_request_id = provider_request_id


class SpeechProviderUncertainError(SpeechProviderError):
    """A remote submission may have been transmitted and cannot be retried safely."""


class SpeechReplacementLineageError(SpeechProviderError):
    """A local replacement-lineage precondition rejected a fresh submission."""


class SpeechAudioStoreError(SpeechGenerationError):
    pass


class SpeechAudioNotFoundError(SpeechAudioStoreError):
    pass


class SpeechAudioConflictError(SpeechAudioStoreError):
    pass


class SpeechAudioIntegrityError(SpeechAudioStoreError):
    pass


class SpeechAudioChecksumError(SpeechAudioIntegrityError):
    pass


class SpeechAudioPathError(SpeechAudioStoreError):
    pass


class SpeechAudioLinkError(SpeechAudioPathError):
    pass


class SpeechManifestError(SpeechGenerationError):
    pass


class SpeechManifestConflictError(SpeechManifestError):
    pass


class SpeechManifestCorruptError(SpeechManifestError):
    pass


class SpeechRemotePreparationError(SpeechGenerationError):
    """Base failure for disabled provider-neutral remote preparation."""


class SpeechCapabilityError(SpeechRemotePreparationError):
    pass


class SpeechCapabilityConfigurationError(SpeechCapabilityError):
    pass


class SpeechVoiceSelectionError(SpeechRemotePreparationError):
    pass


class SpeechCostEstimationError(SpeechRemotePreparationError):
    pass


class SpeechBillableAuthorizationError(SpeechRemotePreparationError):
    pass


class RemoteSpeechProviderDisabledError(SpeechRemotePreparationError):
    pass


class RemoteSpeechJobStoreError(SpeechRemotePreparationError):
    pass


class RemoteSpeechJobConflictError(RemoteSpeechJobStoreError):
    pass


class RemoteSpeechJobCorruptError(RemoteSpeechJobStoreError):
    pass


class SpeechUncertaintyResolutionError(SpeechRemotePreparationError):
    """A durable uncertain-submission resolution is invalid or conflicts."""
