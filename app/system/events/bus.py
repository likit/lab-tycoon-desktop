"""Small in-process domain event bus."""

from __future__ import annotations

from collections import defaultdict
import logging
from typing import Any

from .types import DomainEvent, EventHandler

logger = logging.getLogger("client")


class EventBus:
    """Dispatch domain events to in-process subscribers.

    The bus is intentionally synchronous and minimal. If no handlers are
    subscribed to an event, publishing is a no-op. Handler exceptions are logged
    and swallowed so event publication cannot change existing scenario behavior.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """Register a handler for an event name."""

        if handler not in self._handlers[event_name]:
            self._handlers[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        """Remove a handler from an event name if it is currently registered."""

        handlers = self._handlers.get(event_name)
        if not handlers:
            return

        if handler in handlers:
            handlers.remove(handler)

        if not handlers:
            self._handlers.pop(event_name, None)

    def publish(self, event_name: str, payload: dict[str, Any] | None = None) -> None:
        """Publish an event name and optional payload dictionary."""

        self.publish_event(DomainEvent(name=event_name, payload=payload or {}))

    def publish_event(self, event: DomainEvent) -> None:
        """Publish a typed domain event object."""

        for handler in list(self._handlers.get(event.name, [])):
            try:
                handler(event)
            except Exception:
                logger.exception("Domain event handler failed for %s", event.name)


_global_event_bus = EventBus()


def get_global_event_bus() -> EventBus:
    """Return the process-local event bus used by application code."""

    return _global_event_bus


def publish_domain_event(event_name: str, payload: dict[str, Any] | None = None) -> None:
    """Publish a domain event on the process-local event bus."""

    _global_event_bus.publish(event_name, payload)
