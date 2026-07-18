"""Event bus for decoupled communication between modules."""

from typing import Any, Callable, Coroutine, TypeVar
from collections import defaultdict
import asyncio

T = TypeVar("T")


class EventBus:
    """In-memory event bus for local event-driven communication."""

    def __init__(self):
        self._subscribers: dict[str, list[Callable[[Any], Coroutine[Any, Any, None] | None]]] = (
            defaultdict(list)
        )

    def subscribe(self, event_type: str, handler: Callable[[Any], Coroutine[Any, Any, None] | None]) -> None:
        """Subscribe to an event type."""
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable[[Any], Coroutine[Any, Any, None] | None]) -> None:
        """Unsubscribe from an event type."""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    async def emit(self, event_type: str, payload: Any) -> None:
        """Emit an event to all subscribers."""
        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(payload)
                else:
                    handler(payload)
            except Exception as e:
                # Log but don't crash the bus
                print(f"Event handler error for {event_type}: {e}")

    def emit_sync(self, event_type: str, payload: Any) -> None:
        """Synchronous emit (fire and forget)."""
        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            try:
                if not asyncio.iscoroutinefunction(handler):
                    handler(payload)
            except Exception as e:
                print(f"Event handler error for {event_type}: {e}")
