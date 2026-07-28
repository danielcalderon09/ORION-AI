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
    pass


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
