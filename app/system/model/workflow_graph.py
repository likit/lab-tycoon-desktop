"""Graph primitives for editable workflow layouts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .widget import BaseWidget


@dataclass(slots=True)
class WorkflowNode:
    """A node in the editable workflow graph."""

    node_id: str
    widget: BaseWidget
    kind: str = "task"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the node for persistence."""

        return {
            "id": self.node_id,
            "kind": self.kind,
            "metadata": dict(self.metadata),
            "widget": self.widget.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowNode":
        """Deserialize a graph node from stored workflow data."""

        widget_payload = data.get("widget", {})
        if not isinstance(widget_payload, dict):
            widget_payload = {}

        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        widget = BaseWidget.from_dict(widget_payload)
        combined_metadata = dict(widget.metadata)
        combined_metadata.update(metadata)
        return cls(
            node_id=str(data.get("id", uuid4().hex)),
            widget=widget,
            kind=str(data.get("kind", widget.metadata.get("kind", "task"))),
            metadata=combined_metadata,
        )


@dataclass(slots=True)
class WorkflowEdge:
    """A directional edge between two workflow nodes."""

    source: str
    target: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the edge for persistence."""

        return {
            "source": self.source,
            "target": self.target,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowEdge":
        """Deserialize an edge from stored workflow data."""

        return cls(
            source=str(data.get("source", "")),
            target=str(data.get("target", "")),
            label=str(data.get("label", "")),
        )


class WorkflowGraph:
    """Source-of-truth graph model for editable workflows."""

    def __init__(self) -> None:
        self.nodes: dict[str, WorkflowNode] = {}
        self.edges: list[WorkflowEdge] = []
        self._widget_ids: dict[int, str] = {}
        self._id_counter = 0

    def _unique_node_id(self, base_name: str | None = None) -> str:
        """Generate a stable human-readable node identifier."""

        self._id_counter += 1
        stem = "".join(character.lower() if character.isalnum() else "_" for character in (base_name or "node"))
        stem = stem.strip("_") or "node"
        candidate = f"{stem}_{self._id_counter}"
        while candidate in self.nodes:
            self._id_counter += 1
            candidate = f"{stem}_{self._id_counter}"
        return candidate

    def _unique_node_name(self) -> str:
        """Generate a stable display name for new nodes."""

        return f"Node {len(self.nodes) + 1}"

    def add_widget(
        self,
        widget: BaseWidget,
        *,
        kind: str | None = None,
        metadata: dict[str, Any] | None = None,
        node_id: str | None = None,
    ) -> WorkflowNode:
        """Register a widget as a graph node and return the node."""

        existing_id = self._widget_ids.get(id(widget))
        if existing_id is not None:
            node = self.nodes[existing_id]
            if metadata:
                node.metadata.update(metadata)
                widget.metadata.update(metadata)
            return node

        node_id = node_id or self._unique_node_id(widget.name)
        resolved_kind = kind or str(widget.metadata.get("kind", "task"))
        node = WorkflowNode(
            node_id=node_id,
            widget=widget,
            kind=resolved_kind,
            metadata=dict(metadata or {}),
        )
        node.widget.metadata.setdefault("kind", resolved_kind)
        node.widget.metadata.update(node.metadata)
        self.nodes[node_id] = node
        self._widget_ids[id(widget)] = node_id
        return node

    def add_node(
        self,
        name: str,
        service_time: float = 0.0,
        *,
        kind: str = "task",
        metadata: dict[str, Any] | None = None,
        node_id: str | None = None,
    ) -> WorkflowNode:
        """Create and register a new node with a fresh widget."""

        widget = BaseWidget(name=name, service_time=service_time, metadata=metadata)
        return self.add_widget(widget, kind=kind, metadata=metadata, node_id=node_id)

    def create_default_widget(
        self,
        *,
        node_type: str = "specimens processing",
        name: str | None = None,
        throughput: float = 0.0,
        human_throughput: float = 0.0,
        capacity: int = 1,
        description: str = "",
        required_staff: int = 0,
    ) -> BaseWidget:
        """Create a widget pre-populated with the node form fields."""

        widget = BaseWidget(
            name=name or self._unique_node_name(),
            service_time=float(throughput),
            metadata={
                "node_type": node_type,
                "throughput": float(throughput),
                "human_throughput": float(human_throughput),
                "capacity": max(1, int(capacity)),
                "description": description,
                "required_staff": int(required_staff),
            },
        )
        widget.node_type = node_type
        widget.throughput = throughput
        widget.human_throughput = human_throughput
        widget.capacity = capacity
        widget.description = description
        widget.required_staff = required_staff
        return widget

    def connect_widgets(
        self,
        source_widget: BaseWidget,
        target_widget: BaseWidget,
        *,
        label: str = "",
    ) -> WorkflowEdge:
        """Connect two widgets and keep the graph as the source of truth."""

        source_node = self.add_widget(source_widget)
        target_node = self.add_widget(target_widget)
        return self.connect_nodes(source_node.node_id, target_node.node_id, label=label)

    def connect_nodes(self, source_id: str, target_id: str, *, label: str = "") -> WorkflowEdge:
        """Connect two existing graph nodes."""

        if source_id == target_id:
            raise ValueError("A workflow node cannot connect to itself.")
        if source_id not in self.nodes:
            raise KeyError(source_id)
        if target_id not in self.nodes:
            raise KeyError(target_id)

        edge = WorkflowEdge(source=source_id, target=target_id, label=label)
        if edge not in self.edges:
            self.edges.append(edge)

        source_widget = self.nodes[source_id].widget
        target_widget = self.nodes[target_id].widget
        source_widget.connect(target_widget)
        return edge

    def disconnect_nodes(self, source_id: str, target_id: str) -> None:
        """Remove a connection between two nodes if it exists."""

        source_node = self.nodes.get(source_id)
        target_node = self.nodes.get(target_id)
        if source_node is None or target_node is None:
            return

        self.edges = [
            edge
            for edge in self.edges
            if not (edge.source == source_id and edge.target == target_id)
        ]
        source_node.widget.disconnect(target_node.widget)

    def reassign_connection(self, source_id: str, target_id: str) -> WorkflowEdge:
        """Replace the first outgoing connection from ``source_id`` with ``target_id``."""

        outgoing = self.outgoing_edges(source_id)
        if outgoing:
            self.disconnect_nodes(source_id, outgoing[0].target)
        return self.connect_nodes(source_id, target_id)

    def node_for_widget(self, widget: BaseWidget) -> WorkflowNode | None:
        """Look up the graph node that wraps ``widget``."""

        node_id = self._widget_ids.get(id(widget))
        if node_id is None:
            return None
        return self.nodes.get(node_id)

    def get_node(self, node_id: str) -> WorkflowNode | None:
        """Return a node by identifier."""

        return self.nodes.get(node_id)

    def incoming_edges(self, node_id: str) -> list[WorkflowEdge]:
        """Return all edges pointing into ``node_id``."""

        return [edge for edge in self.edges if edge.target == node_id]

    def outgoing_edges(self, node_id: str) -> list[WorkflowEdge]:
        """Return all edges leaving ``node_id``."""

        return [edge for edge in self.edges if edge.source == node_id]

    def remove_node(self, node_id: str) -> None:
        """Remove a node and every edge attached to it."""

        node = self.nodes.pop(node_id)
        self._widget_ids.pop(id(node.widget), None)
        outgoing_targets = [edge.target for edge in self.edges if edge.source == node_id]
        incoming_sources = [edge.source for edge in self.edges if edge.target == node_id]
        self.edges = [edge for edge in self.edges if edge.source != node_id and edge.target != node_id]

        for target_id in outgoing_targets:
            target_node = self.nodes.get(target_id)
            if target_node is not None:
                node.widget.disconnect(target_node.widget)
        for source_id in incoming_sources:
            source_node = self.nodes.get(source_id)
            if source_node is not None:
                source_node.widget.disconnect(node.widget)

    def rename_node(self, node_id: str, name: str) -> None:
        """Update a node's display name."""

        self.nodes[node_id].widget.name = name

    def update_node_properties(
        self,
        node_id: str,
        *,
        name: str | None = None,
        service_time: float | None = None,
        metadata: dict[str, Any] | None = None,
        kind: str | None = None,
    ) -> None:
        """Update a node and keep widget metadata synchronized."""

        node = self.nodes[node_id]
        if name is not None:
            node.widget.name = name
        if service_time is not None:
            node.widget.service_time = service_time
        if metadata:
            node.metadata.update(metadata)
            node.widget.metadata.update(metadata)
        if kind is not None:
            node.kind = kind
            node.widget.metadata["kind"] = kind

    def root_node_ids(self) -> list[str]:
        """Return nodes without any incoming edges."""

        targets = {edge.target for edge in self.edges}
        return [node_id for node_id in self.nodes if node_id not in targets]

    def iter_nodes(self) -> list[WorkflowNode]:
        """Return nodes in insertion order."""

        return list(self.nodes.values())

    def to_dict(self) -> dict[str, Any]:
        """Serialize the graph and a legacy compatibility payload."""

        node_ids = list(self.nodes.keys())
        legacy_index = {node_id: index for index, node_id in enumerate(node_ids)}
        return {
            "nodes": [self.nodes[node_id].to_dict() for node_id in node_ids],
            "edges": [edge.to_dict() for edge in self.edges],
            "widgets": [self.nodes[node_id].widget.to_dict() for node_id in node_ids],
            "connections": [
                {
                    "source": legacy_index[edge.source],
                    "target": legacy_index[edge.target],
                }
                for edge in self.edges
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowGraph":
        """Deserialize a graph from either the new or legacy payload format."""

        graph = cls()

        if isinstance(data.get("nodes"), list) or isinstance(data.get("edges"), list):
            for node_payload in data.get("nodes", []):
                if not isinstance(node_payload, dict):
                    continue
                node = WorkflowNode.from_dict(node_payload)
                graph.nodes[node.node_id] = node
                graph._widget_ids[id(node.widget)] = node.node_id

            for edge_payload in data.get("edges", []):
                if isinstance(edge_payload, dict):
                    edge = WorkflowEdge.from_dict(edge_payload)
                    if edge.source in graph.nodes and edge.target in graph.nodes:
                        graph.edges.append(edge)
                        graph.nodes[edge.source].widget.connect(graph.nodes[edge.target].widget)
            return graph

        widgets_by_index: list[BaseWidget] = []
        for widget_payload in data.get("widgets", []):
            if not isinstance(widget_payload, dict):
                continue
            widget = BaseWidget.from_dict(widget_payload)
            node = graph.add_widget(widget)
            widgets_by_index.append(node.widget)

        for connection in data.get("connections", []):
            if not isinstance(connection, dict):
                continue
            source_index = int(connection.get("source", -1))
            target_index = int(connection.get("target", -1))
            if 0 <= source_index < len(widgets_by_index) and 0 <= target_index < len(widgets_by_index):
                graph.connect_widgets(widgets_by_index[source_index], widgets_by_index[target_index])

        return graph
