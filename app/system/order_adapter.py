"""Workflow order-arrival adapters.

Adapters create source-independent orders; the workflow simulator remains
responsible for routing the resulting order to downstream nodes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Protocol

from app.system.order_sources import InMemoryCustomer, InMemoryOrder


# Match the application's configured event logger (app.config).
logger = logging.getLogger("client")
ADAPTER_TYPES = ("simulation",)
_SIMULATION_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class OrderAdapter(Protocol):
    """Minimal boundary for producing canonical workflow orders."""

    adapter_type: str

    def create_order(self, sequence: int, *, adapter_id: str, simulation_time: float,
                     patient: InMemoryCustomer | None = None) -> InMemoryOrder:
        """Create one order for arrival at the specified simulation time."""


class SimulationOrderAdapter:
    """Generate deterministic synthetic orders for the existing SimPy engine."""

    adapter_type = "simulation"

    def create_order(self, sequence: int, *, adapter_id: str, simulation_time: float,
                     patient: InMemoryCustomer | None = None) -> InMemoryOrder:
        if sequence < 1:
            raise ValueError("Order sequence must start at 1.")
        if patient is None:
            from app.system.resource_adapter import GeneratedPatientAdapter
            patient = GeneratedPatientAdapter().patient_for_order(sequence)
        patient_id = patient.id
        order = InMemoryOrder(
            id=f"SIM-O{sequence:04d}",
            customer_id=patient_id,
            order_datetime=_SIMULATION_EPOCH + timedelta(hours=simulation_time),
            order_items=[],
            priority="routine",
            customer=patient,
            source=self.adapter_type,
            metadata={"adapter_id": adapter_id, "simulation_time": simulation_time},
        )
        logger.info(
            "order_created order_id=%s source=%s adapter_id=%s simulation_time=%s",
            order.id, order.source, adapter_id, simulation_time,
        )
        return order


def create_order_adapter(adapter_type: str) -> OrderAdapter:
    """Resolve a configured adapter type without coupling the engine to it."""

    if adapter_type == "simulation":
        return SimulationOrderAdapter()
    raise ValueError(f"Unsupported order adapter type: {adapter_type}")
