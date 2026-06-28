"""Workflow graph definitions for the LabTycoon domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .workflow_graph import WorkflowGraph
from .workflow_graph import WorkflowNode

from .specimen import Specimen
from .widget import BaseWidget


@dataclass(slots=True)
class Scenario:
    """A parameter set used to run the same workflow under different conditions.

    Scenarios let one workflow structure be reused with different assumptions,
    such as alternative service times, staffing levels, or specimen arrival
    profiles.
    """

    name: str
    description: str = ""
    widget_service_times: dict[str, float] = field(default_factory=dict)
    parameters: dict[str, float | int | str | bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Serialize the scenario for storage."""

        return {
            "name": self.name,
            "description": self.description,
            "widget_service_times": dict(self.widget_service_times),
            "parameters": dict(self.parameters),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Scenario":
        """Deserialize a scenario from stored data."""

        return cls(
            name=str(data.get("name", "Default Scenario")),
            description=str(data.get("description", "")),
            widget_service_times=dict(data.get("widget_service_times", {})),
            parameters=dict(data.get("parameters", {})),
        )


class Workflow:
    """Store the widget network that defines a simulation model.

    The workflow belongs to the model layer. It tracks structure and entry
    points, but it does not advance time or execute scheduled events.
    """

    def __init__(self) -> None:
        self.graph = WorkflowGraph()
        self.scenarios: list[Scenario] = [Scenario(name="Default Scenario")]

    @property
    def widgets(self) -> list[BaseWidget]:
        """Return widgets in graph insertion order for legacy callers."""

        return [node.widget for node in self.graph.iter_nodes()]

    @widgets.setter
    def widgets(self, widgets: list[BaseWidget]) -> None:
        """Replace the workflow graph with a simple widget chain.

        This setter exists for backward compatibility with older code paths
        that rebuilt workflows by assigning a widget list directly.
        """

        self.graph = WorkflowGraph()
        for widget in widgets:
            self.graph.add_widget(widget)

    def add_widget(self, widget: BaseWidget) -> BaseWidget:
        """Register a widget with the workflow if it is not already present."""

        self.graph.add_widget(widget)
        return widget

    def connect(self, a: BaseWidget, b: BaseWidget) -> None:
        """Connect widget ``a`` to widget ``b`` and register both widgets."""

        self.graph.connect_widgets(a, b)

    def send_to(self, widget: BaseWidget, specimen: Specimen) -> None:
        """Inject a specimen into a specific widget in the workflow."""

        self.add_widget(widget)
        specimen.record_event(widget.name, "injected")
        widget.receive(specimen)

    def add_scenario(self, scenario: Scenario) -> Scenario:
        """Register a scenario for this workflow."""

        if all(existing.name != scenario.name for existing in self.scenarios):
            self.scenarios.append(scenario)
        return scenario

    def to_dict(self) -> dict[str, object]:
        """Serialize the workflow graph and its scenario definitions."""

        payload = self.graph.to_dict()
        payload["graph"] = {
            "nodes": payload["nodes"],
            "edges": payload["edges"],
        }
        payload["scenarios"] = [scenario.to_dict() for scenario in self.scenarios]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Workflow":
        """Deserialize a workflow graph from stored data."""

        workflow = cls()
        graph_payload: dict[str, Any] | None = None
        if isinstance(data.get("graph"), dict):
            graph_payload = dict(data.get("graph", {}))
        elif isinstance(data.get("nodes"), list) or isinstance(data.get("edges"), list):
            graph_payload = {
                "nodes": data.get("nodes", []),
                "edges": data.get("edges", []),
            }
        if graph_payload is not None:
            workflow.graph = WorkflowGraph.from_dict(graph_payload)
        else:
            workflow.graph = WorkflowGraph.from_dict(dict(data))

        scenarios = []
        for scenario_data in data.get("scenarios", []):
            if isinstance(scenario_data, dict):
                scenarios.append(Scenario.from_dict(scenario_data))
        workflow.scenarios = scenarios or [Scenario(name="Default Scenario")]
        return workflow

    def get_node_ids(self) -> list[str]:
        """Return graph node identifiers in insertion order."""

        return list(self.graph.nodes.keys())

    def get_node(self, node_id: str) -> WorkflowNode | None:
        """Return a graph node by identifier."""

        return self.graph.get_node(node_id)

    def create_node(
        self,
        name: str,
        service_time: float = 0.0,
        *,
        kind: str = "task",
        metadata: dict[str, object] | None = None,
    ) -> WorkflowNode:
        """Create a new graph node and return it."""

        return self.graph.add_node(name, service_time, kind=kind, metadata=metadata)

    def create_connected_node(
        self,
        source_node_id: str,
        name: str,
        service_time: float = 0.0,
        *,
        kind: str = "task",
        metadata: dict[str, object] | None = None,
    ) -> WorkflowNode:
        """Create a new node connected from an existing node."""

        source_node = self.graph.get_node(source_node_id)
        if source_node is None:
            raise KeyError(source_node_id)

        node = self.create_node(name, service_time, kind=kind, metadata=metadata)
        self.graph.connect_nodes(source_node_id, node.node_id)
        return node

    def create_branch_node(
        self,
        source_node_id: str,
        name: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> WorkflowNode:
        """Create a branch node connected from an existing node."""

        branch_metadata = dict(metadata or {})
        branch_metadata.setdefault("branch", True)
        return self.create_connected_node(
            source_node_id,
            name,
            0.0,
            kind="branch",
            metadata=branch_metadata,
        )

    def rename_node(self, node_id: str, name: str) -> None:
        """Rename a workflow node."""

        self.graph.rename_node(node_id, name)

    def update_node_properties(
        self,
        node_id: str,
        *,
        name: str | None = None,
        service_time: float | None = None,
        metadata: dict[str, object] | None = None,
        kind: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Update editable node properties."""

        self.graph.update_node_properties(
            node_id,
            name=name,
            service_time=service_time,
            metadata=metadata,
            kind=kind,
            enabled=enabled,
        )

    def delete_node(self, node_id: str) -> None:
        """Delete a workflow node and any attached edges."""

        self.graph.remove_node(node_id)
