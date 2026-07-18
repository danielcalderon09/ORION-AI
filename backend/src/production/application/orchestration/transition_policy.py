"""Pure state-transition policy for production jobs."""

from backend.src.production.domain.enums import ProductionJobStatus


class InvalidProductionTransitionError(ValueError):
    """Raised when a production job state transition is not allowed."""


class TransitionPolicy:
    """Validate job status transitions without infrastructure concerns."""

    @staticmethod
    def allowed_targets(
        current: ProductionJobStatus,
        *,
        allow_failed_recovery: bool = False,
    ) -> set[ProductionJobStatus]:
        targets: dict[ProductionJobStatus, frozenset[ProductionJobStatus]] = {
            ProductionJobStatus.CREATED: frozenset(
                {ProductionJobStatus.QUEUED, ProductionJobStatus.CANCEL_REQUESTED}
            ),
            ProductionJobStatus.QUEUED: frozenset(
                {ProductionJobStatus.RUNNING, ProductionJobStatus.CANCEL_REQUESTED}
            ),
            ProductionJobStatus.RUNNING: frozenset(
                {
                    ProductionJobStatus.WAITING_FOR_RETRY,
                    ProductionJobStatus.NEEDS_USER_ACTION,
                    ProductionJobStatus.CANCEL_REQUESTED,
                    ProductionJobStatus.COMPLETED,
                    ProductionJobStatus.FAILED,
                }
            ),
            ProductionJobStatus.WAITING_FOR_RETRY: frozenset(
                {
                    ProductionJobStatus.QUEUED,
                    ProductionJobStatus.CANCEL_REQUESTED,
                    ProductionJobStatus.FAILED,
                }
            ),
            ProductionJobStatus.NEEDS_USER_ACTION: frozenset(
                {
                    ProductionJobStatus.QUEUED,
                    ProductionJobStatus.CANCEL_REQUESTED,
                    ProductionJobStatus.FAILED,
                }
            ),
            ProductionJobStatus.CANCEL_REQUESTED: frozenset(
                {ProductionJobStatus.CANCELLED, ProductionJobStatus.FAILED}
            ),
            ProductionJobStatus.CANCELLED: frozenset(),
            ProductionJobStatus.COMPLETED: frozenset(),
            ProductionJobStatus.FAILED: frozenset(
                {ProductionJobStatus.QUEUED} if allow_failed_recovery else set()
            ),
        }
        return set(targets[current])

    @classmethod
    def can_transition(
        cls,
        current: ProductionJobStatus,
        target: ProductionJobStatus,
        *,
        allow_failed_recovery: bool = False,
    ) -> bool:
        return target in cls.allowed_targets(
            current,
            allow_failed_recovery=allow_failed_recovery,
        )

    @classmethod
    def validate_transition(
        cls,
        current: ProductionJobStatus,
        target: ProductionJobStatus,
        *,
        allow_failed_recovery: bool = False,
    ) -> None:
        if not cls.can_transition(
            current,
            target,
            allow_failed_recovery=allow_failed_recovery,
        ):
            raise InvalidProductionTransitionError(
                f"invalid production transition: {current.value} -> {target.value}"
            )
