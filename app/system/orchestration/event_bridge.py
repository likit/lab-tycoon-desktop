"""Passive bridge from domain events to workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from ..events import DomainEvent, DomainEventName, EventBus
from .facade import WorkflowFacade


DEFAULT_WORKFLOW_EVENT_NAMES = (
    DomainEventName.ORDER_CREATED,
    DomainEventName.SAMPLE_RECEIVED,
    DomainEventName.RESULT_GENERATED,
    DomainEventName.QC_FAILED,
    DomainEventName.CRITICAL_VALUE_DETECTED,
)


@dataclass(frozen=True, slots=True)
class ObservedWorkflowEvent:
    """A domain event observed by the workflow bridge."""

    name: str
    payload: dict
    workflow_id: str | None = None


class WorkflowEventBridge:
    """Subscribe workflow orchestration to domain events without side effects.

    The bridge records observed events in memory. When the workflow facade is
    disabled, this remains purely passive. When enabled, it may create a stub
    workflow instance for observability only; it does not alter LIS, UI, or
    simulation state.
    """

    def __init__(
        self,
        facade: WorkflowFacade | None = None,
        event_names: tuple[str, ...] = DEFAULT_WORKFLOW_EVENT_NAMES,
    ) -> None:
        self.facade = facade or WorkflowFacade()
        self.event_names = event_names
        self.observed_events: list[ObservedWorkflowEvent] = []

    def register(self, event_bus: EventBus) -> None:
        """Subscribe the bridge to configured domain event names."""

        for event_name in self.event_names:
            event_bus.subscribe(event_name, self.handle_event)

    def unregister(self, event_bus: EventBus) -> None:
        """Unsubscribe the bridge from configured domain event names."""

        for event_name in self.event_names:
            event_bus.unsubscribe(event_name, self.handle_event)

    def handle_event(self, event: DomainEvent) -> None:
        """Observe a domain event and optionally forward it to the stub facade."""

        workflow_id = None
        if self.facade.enabled:
            if event.name == DomainEventName.ORDER_CREATED:
                workflow_id = self.facade.start_workflow(
                    "duplicate_order_review",
                    {"event_name": event.name, "payload": dict(event.payload)},
                )
            else:
                workflow_id = self.facade.observe_domain_event(event.name, dict(event.payload))

        self.observed_events.append(
            ObservedWorkflowEvent(
                name=event.name,
                payload=dict(event.payload),
                workflow_id=workflow_id,
            )
        )
