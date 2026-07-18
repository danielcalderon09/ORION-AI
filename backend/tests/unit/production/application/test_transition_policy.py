"""Unit tests for production job state transitions."""

import pytest

from backend.src.production.application.orchestration import (
    InvalidProductionTransitionError,
    TransitionPolicy,
)
from backend.src.production.domain.enums import ProductionJobStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ProductionJobStatus.CREATED, ProductionJobStatus.QUEUED),
        (ProductionJobStatus.CREATED, ProductionJobStatus.CANCEL_REQUESTED),
        (ProductionJobStatus.QUEUED, ProductionJobStatus.RUNNING),
        (ProductionJobStatus.RUNNING, ProductionJobStatus.WAITING_FOR_RETRY),
        (ProductionJobStatus.RUNNING, ProductionJobStatus.NEEDS_USER_ACTION),
        (ProductionJobStatus.RUNNING, ProductionJobStatus.COMPLETED),
        (ProductionJobStatus.RUNNING, ProductionJobStatus.FAILED),
        (ProductionJobStatus.WAITING_FOR_RETRY, ProductionJobStatus.QUEUED),
        (ProductionJobStatus.NEEDS_USER_ACTION, ProductionJobStatus.QUEUED),
        (ProductionJobStatus.CANCEL_REQUESTED, ProductionJobStatus.CANCELLED),
    ],
)
def test_transition_policy_accepts_valid_transitions(
    current: ProductionJobStatus,
    target: ProductionJobStatus,
) -> None:
    assert TransitionPolicy.can_transition(current, target)
    TransitionPolicy.validate_transition(current, target)


def test_transition_policy_rejects_invalid_transition() -> None:
    with pytest.raises(InvalidProductionTransitionError, match="created -> completed"):
        TransitionPolicy.validate_transition(
            ProductionJobStatus.CREATED,
            ProductionJobStatus.COMPLETED,
        )


def test_failed_does_not_return_to_queued_by_default() -> None:
    assert not TransitionPolicy.can_transition(
        ProductionJobStatus.FAILED,
        ProductionJobStatus.QUEUED,
    )

    with pytest.raises(InvalidProductionTransitionError):
        TransitionPolicy.validate_transition(
            ProductionJobStatus.FAILED,
            ProductionJobStatus.QUEUED,
        )


def test_failed_can_return_to_queued_with_explicit_recovery() -> None:
    assert TransitionPolicy.can_transition(
        ProductionJobStatus.FAILED,
        ProductionJobStatus.QUEUED,
        allow_failed_recovery=True,
    )
    TransitionPolicy.validate_transition(
        ProductionJobStatus.FAILED,
        ProductionJobStatus.QUEUED,
        allow_failed_recovery=True,
    )


@pytest.mark.parametrize(
    "status",
    [ProductionJobStatus.CANCELLED, ProductionJobStatus.COMPLETED],
)
def test_terminal_states_have_no_allowed_targets(status: ProductionJobStatus) -> None:
    assert TransitionPolicy.allowed_targets(status) == set()
