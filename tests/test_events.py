"""Tests for the lightweight domain event bus."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.system.events import DomainEvent, DomainEventName, EventBus
from app.system.orchestration import WorkflowConfig, WorkflowEventBridge, WorkflowFacade


class DomainEventBusTests(unittest.TestCase):
    def test_publish_with_no_subscribers_does_not_raise(self) -> None:
        event_bus = EventBus()

        event_bus.publish(DomainEventName.ORDER_CREATED, {"order_id": "1"})

    def test_subscriber_receives_published_event(self) -> None:
        event_bus = EventBus()
        received = []

        event_bus.subscribe(DomainEventName.ORDER_CREATED, received.append)
        event_bus.publish(DomainEventName.ORDER_CREATED, {"order_id": "1"})

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].name, DomainEventName.ORDER_CREATED)
        self.assertEqual(received[0].payload["order_id"], "1")

    def test_multiple_subscribers_receive_event(self) -> None:
        event_bus = EventBus()
        first = []
        second = []

        event_bus.subscribe(DomainEventName.SAMPLE_RECEIVED, first.append)
        event_bus.subscribe(DomainEventName.SAMPLE_RECEIVED, second.append)
        event_bus.publish_event(
            DomainEvent(
                name=DomainEventName.SAMPLE_RECEIVED,
                payload={"specimen_id": "tube-1"},
            )
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)

    def test_unsubscribe_removes_handler(self) -> None:
        event_bus = EventBus()
        received = []

        event_bus.subscribe(DomainEventName.RESULT_GENERATED, received.append)
        event_bus.unsubscribe(DomainEventName.RESULT_GENERATED, received.append)
        event_bus.publish(DomainEventName.RESULT_GENERATED, {"result_id": "1"})

        self.assertEqual(received, [])

    def test_subscriber_exception_does_not_stop_dispatch(self) -> None:
        event_bus = EventBus()
        received = []

        def failing_handler(event: DomainEvent) -> None:
            raise RuntimeError("handler failed")

        event_bus.subscribe(DomainEventName.QC_FAILED, failing_handler)
        event_bus.subscribe(DomainEventName.QC_FAILED, received.append)

        with patch("app.system.events.bus.logger.exception") as log_exception:
            event_bus.publish(DomainEventName.QC_FAILED, {"qc_id": "1"})

        self.assertEqual(len(received), 1)
        log_exception.assert_called_once()


class WorkflowEventBridgeTests(unittest.TestCase):
    def test_bridge_is_passive_when_workflow_support_is_disabled(self) -> None:
        event_bus = EventBus()
        facade = WorkflowFacade(config=WorkflowConfig(enabled=False))
        bridge = WorkflowEventBridge(
            facade=facade
        )
        bridge.register(event_bus)

        event_bus.publish(DomainEventName.ORDER_CREATED, {"order_id": "1"})

        self.assertEqual(len(bridge.observed_events), 1)
        self.assertEqual(bridge.observed_events[0].name, DomainEventName.ORDER_CREATED)
        self.assertIsNone(bridge.observed_events[0].workflow_id)
        self.assertEqual(facade.get_observations(), [])

    def test_enabled_bridge_uses_stub_runtime_only(self) -> None:
        event_bus = EventBus()
        facade = WorkflowFacade(config=WorkflowConfig(enabled=True))
        bridge = WorkflowEventBridge(facade=facade)
        bridge.register(event_bus)

        event_bus.publish(DomainEventName.CRITICAL_VALUE_DETECTED, {"result_id": "1"})

        self.assertEqual(len(bridge.observed_events), 1)
        self.assertIsNotNone(bridge.observed_events[0].workflow_id)
        self.assertEqual(facade.get_observations(), [])

    def test_enabled_bridge_creates_passive_order_observation(self) -> None:
        event_bus = EventBus()
        facade = WorkflowFacade(config=WorkflowConfig(enabled=True))
        bridge = WorkflowEventBridge(facade=facade)
        bridge.register(event_bus)

        event_bus.publish(
            DomainEventName.ORDER_CREATED,
            {"order_id": "3", "duplicate_candidate": True},
        )

        observations = facade.get_observations(entity_type="order", entity_id="3")
        self.assertEqual(len(observations), 1)
        self.assertEqual(
            observations[0]["recommendation"],
            "Possible duplicate order review recommended",
        )
        flags = facade.get_entity_flags("order", "3")
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["kind"], "review_flag")

    def test_bridge_can_unregister(self) -> None:
        event_bus = EventBus()
        bridge = WorkflowEventBridge(
            facade=WorkflowFacade(config=WorkflowConfig(enabled=False))
        )
        bridge.register(event_bus)
        bridge.unregister(event_bus)

        event_bus.publish(DomainEventName.ORDER_CREATED, {"order_id": "1"})

        self.assertEqual(bridge.observed_events, [])


if __name__ == "__main__":
    unittest.main()
