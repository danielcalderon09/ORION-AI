"""Non-functional remote speech placeholder with no transport dependencies."""

from backend.src.production.speech_generation.exceptions import (
    RemoteSpeechProviderDisabledError,
)
from backend.src.production.speech_generation.remote_ports import (
    AsynchronousSpeechSubmission,
    RemoteSpeechGenerationRequest,
    RemoteSpeechPollResult,
    SynchronousSpeechResult,
)


class DisabledRemoteSpeechProvider:
    """Always fail before external activity; validates composition gates only."""

    name = "disabled"

    async def generate_synchronously(
        self,
        request: RemoteSpeechGenerationRequest,
    ) -> SynchronousSpeechResult:
        del request
        raise RemoteSpeechProviderDisabledError("remote speech provider is disabled")

    async def submit(
        self,
        request: RemoteSpeechGenerationRequest,
    ) -> AsynchronousSpeechSubmission:
        del request
        raise RemoteSpeechProviderDisabledError("remote speech provider is disabled")

    async def poll(self, *, remote_job_id: str) -> RemoteSpeechPollResult:
        del remote_job_id
        raise RemoteSpeechProviderDisabledError("remote speech provider is disabled")

    async def download(self, *, remote_job_id: str) -> SynchronousSpeechResult:
        del remote_job_id
        raise RemoteSpeechProviderDisabledError("remote speech provider is disabled")

    async def close(self) -> None:
        return None
