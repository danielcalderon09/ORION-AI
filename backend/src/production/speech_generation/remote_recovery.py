"""Pure recovery classification for future remote speech execution."""

from enum import StrEnum

from backend.src.production.domain.base import ContractModel
from backend.src.production.speech_generation.cost import (
    SpeechCostAuthorizationStatus,
)
from backend.src.production.speech_generation.remote_models import (
    RemoteSpeechJobRecord,
    RemoteSpeechJobStatus,
)


class RemoteSpeechRecoveryAction(StrEnum):
    PROCEED_TO_SUBMISSION = "proceed_to_submission"
    MARK_UNCERTAIN = "mark_uncertain"
    POLL = "poll"
    DOWNLOAD = "download"
    RECOVER_LOCAL_AUDIO = "recover_local_audio"
    STOP_TERMINAL = "stop_terminal"
    REQUIRE_MANUAL_REVIEW = "require_manual_review"


class RemoteSpeechRecoveryDecision(ContractModel):
    action: RemoteSpeechRecoveryAction
    safe_to_submit: bool
    reason: str


class RemoteSpeechRecoveryPolicy:
    def classify(
        self,
        *,
        record: RemoteSpeechJobRecord,
        verified_local_audio: bool,
    ) -> RemoteSpeechRecoveryDecision:
        if verified_local_audio:
            return RemoteSpeechRecoveryDecision(
                action=RemoteSpeechRecoveryAction.RECOVER_LOCAL_AUDIO,
                safe_to_submit=False,
                reason="verified local speech audio already exists",
            )
        if record.status is RemoteSpeechJobStatus.PREPARED:
            if (
                record.authorization is None
                or record.authorization.status is not SpeechCostAuthorizationStatus.AUTHORIZED
            ):
                return RemoteSpeechRecoveryDecision(
                    action=RemoteSpeechRecoveryAction.REQUIRE_MANUAL_REVIEW,
                    safe_to_submit=False,
                    reason="prepared remote speech lacks explicit cost authorization",
                )
            return RemoteSpeechRecoveryDecision(
                action=RemoteSpeechRecoveryAction.PROCEED_TO_SUBMISSION,
                safe_to_submit=True,
                reason="durable request was prepared but never submitted",
            )
        if record.status is RemoteSpeechJobStatus.SUBMITTING:
            return RemoteSpeechRecoveryDecision(
                action=RemoteSpeechRecoveryAction.MARK_UNCERTAIN,
                safe_to_submit=False,
                reason="submission outcome is ambiguous without durable remote identity",
            )
        if record.status is RemoteSpeechJobStatus.UNCERTAIN:
            return RemoteSpeechRecoveryDecision(
                action=RemoteSpeechRecoveryAction.REQUIRE_MANUAL_REVIEW,
                safe_to_submit=False,
                reason="uncertain billable submission must never be retried automatically",
            )
        if record.status in {
            RemoteSpeechJobStatus.SUBMITTED,
            RemoteSpeechJobStatus.PENDING,
            RemoteSpeechJobStatus.PROCESSING,
        }:
            return RemoteSpeechRecoveryDecision(
                action=RemoteSpeechRecoveryAction.POLL,
                safe_to_submit=False,
                reason="durable remote identity may be polled without resubmission",
            )
        if record.status is RemoteSpeechJobStatus.COMPLETED:
            if record.output is None:
                return RemoteSpeechRecoveryDecision(
                    action=RemoteSpeechRecoveryAction.DOWNLOAD,
                    safe_to_submit=False,
                    reason="completed remote speech output has not been downloaded",
                )
            return RemoteSpeechRecoveryDecision(
                action=RemoteSpeechRecoveryAction.REQUIRE_MANUAL_REVIEW,
                safe_to_submit=False,
                reason="downloaded output is missing verified local audio",
            )
        return RemoteSpeechRecoveryDecision(
            action=RemoteSpeechRecoveryAction.STOP_TERMINAL,
            safe_to_submit=False,
            reason="terminal remote speech failure cannot be resubmitted automatically",
        )
