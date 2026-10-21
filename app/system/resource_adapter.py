"""Non-routing workflow resources used when constructing simulation orders."""

from __future__ import annotations

from typing import Protocol

from app.system.model import Workflow
from app.system.order_sources import InMemoryCustomer


class PatientResourceAdapter(Protocol):
    def patient_for_order(self, sequence: int) -> InMemoryCustomer:
        """Return a patient for a generated order in the current run."""


class GeneratedPatientAdapter:
    """Generate deterministic, run-scoped mock identities."""

    def patient_for_order(self, sequence: int) -> InMemoryCustomer:
        if sequence < 1:
            raise ValueError("Patient sequence must start at 1.")
        patient_id = f"SIM-P{sequence:04d}"
        return InMemoryCustomer(patient_id, patient_id, f"Simulated Patient {sequence}")


def patient_adapter_for_workflow(workflow: Workflow) -> PatientResourceAdapter:
    """Resolve a patient dependency; retain legacy generation when unlinked."""

    resources = [
        node for node in workflow.graph.iter_nodes()
        if node.kind == "resource" and node.metadata.get("resource_type") == "patient"
        and node.widget.enabled
    ]
    if len(resources) > 1:
        raise ValueError("Only one enabled patient Resource node is supported per workflow.")
    if resources:
        node = resources[0]
        if workflow.graph.incoming_edges(node.node_id):
            raise ValueError("Patient Resource nodes cannot have incoming connections.")
        for edge in workflow.graph.outgoing_edges(node.node_id):
            target = workflow.get_node(edge.target)
            if target is None or target.widget.node_type != "order adapter":
                raise ValueError("Patient Resources may only connect to Order Adapters.")
        if node.metadata.get("adapter_type") != "generate_per_run":
            raise ValueError("Unsupported patient Resource adapter type.")
    return GeneratedPatientAdapter()
