"""Tests for workflow order-arrival adapters."""

from __future__ import annotations

import unittest

from app.system.order_adapter import SimulationOrderAdapter, create_order_adapter
from app.system.order_sources import InMemoryOrder
from app.system.resource_adapter import patient_adapter_for_workflow
from app.system.model import Workflow


class SimulationOrderAdapterTests(unittest.TestCase):
    def test_factory_creates_canonical_simulation_order(self) -> None:
        adapter = create_order_adapter("simulation")

        with self.assertLogs("client", level="INFO") as captured:
            order = adapter.create_order(2, adapter_id="adapter_1", simulation_time=1.5)

        self.assertIsInstance(adapter, SimulationOrderAdapter)
        self.assertIsInstance(order, InMemoryOrder)
        self.assertEqual(order.id, "SIM-O0002")
        self.assertEqual(order.customer_id, "SIM-P0002")
        self.assertEqual(order.priority, "routine")
        self.assertEqual(order.order_items, [])
        self.assertEqual(order.source, "simulation")
        self.assertEqual(order.metadata["adapter_id"], "adapter_1")
        self.assertEqual(order.metadata["simulation_time"], 1.5)
        self.assertIn("order_created", captured.output[0])

    def test_unknown_adapter_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported order adapter"):
            create_order_adapter("his")

    def test_patient_resource_adapter_validates_configuration(self) -> None:
        workflow = Workflow()
        patient = workflow.create_node(
            "Patients", kind="resource",
            metadata={"node_type": "resource", "resource_type": "patient",
                      "adapter_type": "generate_per_run"},
        )
        self.assertEqual(patient_adapter_for_workflow(workflow).patient_for_order(1).hn, "SIM-P0001")
        patient.metadata["adapter_type"] = "unknown"
        with self.assertRaisesRegex(ValueError, "Unsupported patient Resource"):
            patient_adapter_for_workflow(workflow)
