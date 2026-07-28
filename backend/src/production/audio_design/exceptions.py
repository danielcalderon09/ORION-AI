"""Typed failures for durable offline audio design."""


class AudioDesignError(Exception):
    """Base failure for the audio-design bounded context."""


class AudioDesignConfigurationError(AudioDesignError):
    """Raised when configured offline limits are incompatible."""


class AudioDesignPlanError(AudioDesignError):
    """Raised when explicit script audio metadata is unsafe or invalid."""


class AudioDesignSourceError(AudioDesignError):
    """Base durable source-script failure."""


class AudioDesignSourceNotFoundError(AudioDesignSourceError):
    """Raised when the durable source script cannot be found."""


class AudioDesignSourceIntegrityError(AudioDesignSourceError):
    """Raised when the durable source script fails verification."""


class AudioDesignProviderError(AudioDesignError):
    """Base simulated provider failure."""


class AudioDesignProviderClosedError(AudioDesignProviderError):
    """Raised when a closed provider is used."""


class AudioDesignProviderResponseError(AudioDesignProviderError):
    """Raised when generated audio violates the provider-neutral contract."""


class AudioDesignWavError(AudioDesignError):
    """Raised when PCM WAV content is invalid."""


class AudioDesignStoreError(AudioDesignError):
    """Base durable audio-store failure."""


class AudioDesignStoreConflictError(AudioDesignStoreError):
    """Raised when a write-once path already has incompatible content."""


class AudioDesignStoreIntegrityError(AudioDesignStoreError):
    """Raised when stored audio fails integrity validation."""


class AudioDesignStoreNotFoundError(AudioDesignStoreError):
    """Raised when expected audio is missing."""


class AudioDesignStorePathError(AudioDesignStoreError):
    """Raised when an audio path is unsafe."""


class AudioDesignManifestError(AudioDesignError):
    """Base durable manifest failure."""


class AudioDesignManifestConflictError(AudioDesignManifestError):
    """Raised for stale CAS writes or conflicting manifest identity."""


class AudioDesignManifestCorruptError(AudioDesignManifestError):
    """Raised when durable manifest bytes are invalid."""
