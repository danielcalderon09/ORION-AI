"""Dictionary-backed dispatch of stages to handlers."""

from collections.abc import Iterable

from backend.src.production.domain.enums import ProductionStage
from backend.src.production.runtime.handlers import StageHandler


class StageHandlerRegistrationError(ValueError):
    """Raised for ambiguous handler registration."""


class StageHandlerNotFoundError(LookupError):
    """Raised when an executable stage has no handler."""


class StageHandlerRegistry:
    def __init__(self, handlers: Iterable[StageHandler]) -> None:
        self._handlers: dict[ProductionStage, StageHandler] = {}
        for handler in handlers:
            if not handler.supported_stages:
                raise StageHandlerRegistrationError("handler must support at least one stage")
            for stage in handler.supported_stages:
                if stage in self._handlers:
                    raise StageHandlerRegistrationError(
                        f"multiple handlers registered for {stage.value}"
                    )
                self._handlers[stage] = handler

    def resolve(self, stage: ProductionStage) -> StageHandler:
        try:
            return self._handlers[stage]
        except KeyError as exc:
            raise StageHandlerNotFoundError(f"no handler registered for {stage.value}") from exc

    @property
    def registered_stages(self) -> frozenset[ProductionStage]:
        return frozenset(self._handlers)
