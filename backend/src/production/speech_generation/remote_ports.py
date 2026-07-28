"""Separate provider-neutral ports for future remote speech API styles."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import Field

from backend.src.production.domain.base import ContractModel
from backend.src.production.speech_generation.remote_capabilities import (
    SpeechCapabilitySnapshot,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobRecord,
    RemoteSpeechJobStatus,
    RemoteSpeechOutputMetadata,
)


class SpeechCapabilitySource(Protocol):
    async def discover_capabilities(self) -> SpeechCapabilitySnapshot: ...

    async def close(self) -> None: ...


class RemoteSpeechJobStore(Protocol):
    async def read(
        self,
        *,
        job_id: UUID,
        attempt_number: int,
        segment_id: str,
    ) -> RemoteSpeechJobRecord | None: ...

    async def create(self, record: RemoteSpeechJobRecord) -> None: ...

    async def checkpoint(
        self,
        *,
        previous: RemoteSpeechJobRecord,
        current: RemoteSpeechJobRecord,
    ) -> None: ...

    async def list_records(self) -> tuple[RemoteSpeechJobRecord, ...]: ...


class RemoteSpeechGenerationRequest(ContractModel):
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    narration_text: str = Field(repr=False, exclude=True, min_length=1)


class SynchronousSpeechResult(ContractModel):
    content: bytes = Field(repr=False, exclude=True, min_length=1)
    output: RemoteSpeechOutputMetadata


class AsynchronousSpeechSubmission(ContractModel):
    remote_job_id: str = Field(min_length=1, max_length=300)
    remote_generation_id: str | None = Field(default=None, max_length=300)
    status: RemoteSpeechJobStatus


class RemoteSpeechPollResult(ContractModel):
    remote_job_id: str = Field(min_length=1, max_length=300)
    remote_generation_id: str | None = Field(default=None, max_length=300)
    status: RemoteSpeechJobStatus
    safe_error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,100}$")


class SynchronousSpeechProvider(Protocol):
    async def generate_synchronously(
        self,
        request: RemoteSpeechGenerationRequest,
    ) -> SynchronousSpeechResult: ...

    async def close(self) -> None: ...


class AsynchronousSpeechSubmitter(Protocol):
    async def submit(
        self,
        request: RemoteSpeechGenerationRequest,
    ) -> AsynchronousSpeechSubmission: ...

    async def close(self) -> None: ...


class SpeechRemoteStatusProvider(Protocol):
    async def poll(self, *, remote_job_id: str) -> RemoteSpeechPollResult: ...


class SpeechRemoteAudioDownloader(Protocol):
    async def download(self, *, remote_job_id: str) -> SynchronousSpeechResult: ...
