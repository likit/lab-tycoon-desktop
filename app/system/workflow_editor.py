"""Workflow editor UI for graph-based workflow documents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import FreeSimpleGUI as sg
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.system.models import Test, engine
from app.system.model import Scenario, Workflow, WorkflowNode
from app.system.storage import WorkflowDocument, WorkflowRepository


logger = logging.getLogger(__name__)


def _notify(message: str) -> None:
    """Show a lightweight non-blocking workflow notification."""

    sg.popup_non_blocking(
        message,
        title="Workflow",
        background_color="lightyellow",
        keep_on_top=True,
    )


def _reset_scrollable_view(element: sg.Element) -> None:
    """Move a scrollable container back to its origin if supported."""

    widget = getattr(element, "Widget", None)
    if widget is None:
        return

    for method_name in ("xview_moveto", "yview_moveto"):
        method = getattr(widget, method_name, None)
        if callable(method):
            try:
                method(0)
            except Exception:
                continue


def _bind_graph_right_click_selection(
    graph: sg.Graph,
    controller: "WorkflowEditorController",
    window: sg.Window,
) -> None:
    """Select the node under a right click without blocking the native menu."""

    def handle_right_click(event: Any) -> None:
        try:
            graph_x, graph_y = graph._convert_canvas_xy_to_xy(event.x, event.y)
        except Exception:
            return

        selected_node_id = controller.select_node_at(graph_x, graph_y)
        if selected_node_id is not None:
            controller.refresh_summary(window)
            controller.rerender(window)
            controller.update_graph_context_menu(window)

    for bind_string in ("<ButtonPress-3>", "<ButtonPress-2>"):
        try:
            graph.Widget.bind(bind_string, handle_right_click, add="+")
        except Exception:
            continue


def _bind_graph_double_click_properties(
    graph: sg.Graph,
    controller: "WorkflowEditorController",
    window: sg.Window,
) -> None:
    """Open the node properties editor when a node is double-clicked."""

    def handle_double_click(event: Any) -> None:
        try:
            graph_x, graph_y = graph._convert_canvas_xy_to_xy(event.x, event.y)
        except Exception:
            return

        selected_node_id = controller.select_node_at(graph_x, graph_y)
        if selected_node_id is None:
            return

        try:
            window.write_event_value("-WF-GRAPH-DOUBLE-", selected_node_id)
        except Exception:
            pass

    try:
        graph.Widget.bind("<Double-Button-1>", handle_double_click, add="+")
    except Exception:
        return


def _show_node_form_dialog(node: WorkflowNode | None) -> dict[str, Any] | None:
    """Prompt for node configuration values."""

    if node is None:
        return None

    node_types = [
        "specimens processing",
        "automated analyzer",
        "manual analyzer",
        "result reporting",
        "result validating",
        "AI agent",
    ]
    current_type = node.widget.node_type if node.widget.node_type in node_types else node.kind
    layout = [
        [
            sg.Text("Node type"),
            sg.Combo(
                node_types,
                default_value=current_type,
                key="-NODE-TYPE-",
                readonly=True,
                expand_x=True,
            ),
        ],
        [sg.Text("Name"), sg.Input(node.widget.name, key="-NAME-", expand_x=True)],
        [
            sg.Text("Processing Capacity (per hours)"),
            sg.Input(str(node.widget.throughput), key="-THROUGHPUT-", size=(14, 1)),
        ],
        [
            sg.Text("Human Processing Capacity (per hour)"),
            sg.Input(str(node.widget.human_throughput), key="-HUMAN-THROUGHPUT-", size=(14, 1)),
        ],
        [
            sg.Text("Capacity"),
            sg.Input(str(node.widget.capacity), key="-CAPACITY-", size=(14, 1)),
        ],
        [
            sg.Text("Required staff"),
            sg.Input(str(node.widget.required_staff), key="-STAFF-", size=(14, 1)),
        ],
        [
            sg.Text("Description"),
            sg.Multiline(node.widget.description, key="-DESC-", size=(46, 4), expand_x=True),
        ],
        [sg.Button("Save", key="-SAVE-", bind_return_key=True), sg.Button("Cancel")],
    ]
    window = sg.Window("Node Properties", layout=layout, modal=True, finalize=True, keep_on_top=True)
    try:
        while True:
            event, values = window.read()
            if event in ("Cancel", sg.WIN_CLOSED):
                return None
            if event == "-SAVE-":
                try:
                    throughput = float(values["-THROUGHPUT-"])
                    human_throughput = float(values["-HUMAN-THROUGHPUT-"])
                    capacity = int(values["-CAPACITY-"])
                    required_staff = int(values["-STAFF-"])
                except (TypeError, ValueError):
                    _notify(
                        "Processing capacity and human processing capacity must be numeric, capacity and staff must be integers."
                    )
                    continue

                name = str(values["-NAME-"]).strip() or "Unnamed Node"
                description = str(values["-DESC-"]).strip()
                node_type = str(values["-NODE-TYPE-"])
                return {
                    "node_type": node_type,
                    "name": name,
                    "description": description,
                    "throughput": throughput,
                    "human_throughput": human_throughput,
                    "capacity": capacity,
                    "required_staff": required_staff,
                }
    finally:
        window.close()


def _load_available_tests() -> list[dict[str, str]]:
    """Load available lab tests for analyzer configuration."""

    try:
        with Session(engine) as session:
            return [
                {
                    "code": str(test.code),
                    "label": str(test.label),
                }
                for test in session.scalars(select(Test).order_by(Test.code))
            ]
    except Exception:
        logger.exception("Unable to load available tests for analyzer configuration.")
        return []


def _show_test_selection_dialog(
    *,
    node: WorkflowNode,
    available_tests: list[dict[str, str]],
) -> list[str] | None:
    """Prompt for analyzer test selection using checkboxes."""

    selected_codes = set(node.widget.metadata.get("analyzer_tests", []))
    if not available_tests:
        _notify("No tests are available to select.")
        return None

    layout: list[list[Any]] = [
        [sg.Text(f"Configure tests for {node.widget.name}", font=("Arial", 12, "bold"))],
        [sg.Text("Select the tests this analyzer can perform:")],
        [
            sg.Column(
                [
                    [
                        sg.Checkbox(
                            f"{test['code']} - {test['label']}",
                            default=test["code"] in selected_codes,
                            key=f"-TEST-{index}-",
                        )
                    ]
                    for index, test in enumerate(available_tests)
                ],
                scrollable=True,
                vertical_scroll_only=True,
                size=(520, 320),
                expand_x=True,
                expand_y=True,
            )
        ],
        [
            sg.Button("Select All", key="-SELECT-ALL-"),
            sg.Button("Clear All", key="-CLEAR-ALL-"),
            sg.Button("Save", key="-SAVE-TESTS-", bind_return_key=True),
            sg.Button("Cancel"),
        ],
    ]
    window = sg.Window(
        "Analyzer Tests",
        layout=layout,
        modal=True,
        finalize=True,
        keep_on_top=True,
    )
    try:
        while True:
            event, values = window.read()
            if event in ("Cancel", sg.WIN_CLOSED):
                return None
            if event == "-SELECT-ALL-":
                for index in range(len(available_tests)):
                    window[f"-TEST-{index}-"].update(value=True)
            elif event == "-CLEAR-ALL-":
                for index in range(len(available_tests)):
                    window[f"-TEST-{index}-"].update(value=False)
            elif event == "-SAVE-TESTS-":
                return [
                    test["code"]
                    for index, test in enumerate(available_tests)
                    if values.get(f"-TEST-{index}-")
                ]
    finally:
        window.close()


@dataclass(slots=True)
class NodeBounds:
    """Canvas bounds for hit testing and drawing."""

    left: float
    right: float
    top: float
    bottom: float

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.bottom <= y <= self.top


class WorkflowLayoutEngine:
    """Compute a simple top-to-bottom layout for a workflow graph."""

    def __init__(
        self,
        *,
        canvas_width: int = 1300,
        canvas_height: int = 900,
        node_width: int = 180,
        node_height: int = 72,
        branch_size: int = 86,
        column_gap: int = 260,
        row_gap: int = 145,
        left_margin: int = 90,
        top_margin: int = 110,
    ) -> None:
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.node_width = node_width
        self.node_height = node_height
        self.branch_size = branch_size
        self.column_gap = column_gap
        self.row_gap = row_gap
        self.left_margin = left_margin
        self.top_margin = top_margin

    def layout(self, workflow: Workflow) -> dict[str, tuple[float, float]]:
        """Return node center points keyed by node id."""

        graph = workflow.graph
        if not graph.nodes:
            return {}

        levels = self._assign_levels(graph)
        grouped: dict[int, list[str]] = {}
        for node_id, level in levels.items():
            grouped.setdefault(level, []).append(node_id)

        positions: dict[str, tuple[float, float]] = {}
        max_level = max(grouped) if grouped else 0
        for level in sorted(grouped):
            node_ids = sorted(
                grouped[level],
                key=lambda node_id: graph.nodes[node_id].widget.name.lower(),
            )
            total_width = max(0, (len(node_ids) - 1) * self.column_gap)
            start_x = max(self.left_margin, (self.canvas_width - total_width) / 2)
            y = self.canvas_height - self.top_margin - level * self.row_gap
            if y < self.top_margin:
                y = self.top_margin + (max_level - level) * 20
            for index, node_id in enumerate(node_ids):
                positions[node_id] = (start_x + index * self.column_gap, y)
        return positions

    def node_size(self, node: WorkflowNode) -> tuple[int, int]:
        """Return the render size for a node."""

        if node.kind == "branch":
            return self.branch_size, self.branch_size
        return self.node_width, self.node_height

    def node_bounds(self, node: WorkflowNode, center_x: float, center_y: float) -> NodeBounds:
        """Return hit-test bounds for a node."""

        width, height = self.node_size(node)
        half_w = width / 2
        half_h = height / 2
        return NodeBounds(
            left=center_x - half_w,
            right=center_x + half_w,
            top=center_y + half_h,
            bottom=center_y - half_h,
        )

    def _assign_levels(self, graph: Any) -> dict[str, int]:
        """Assign a row index to each node using a light-weight DAG walk."""

        indegree = {node_id: 0 for node_id in graph.nodes}
        for edge in graph.edges:
            if edge.target in indegree:
                indegree[edge.target] += 1

        levels: dict[str, int] = {node_id: 0 for node_id, degree in indegree.items() if degree == 0}
        if not levels:
            first_node = next(iter(graph.nodes))
            levels[first_node] = 0

        changed = True
        while changed:
            changed = False
            for edge in graph.edges:
                if edge.source not in levels:
                    continue
                candidate_level = levels[edge.source] + 1
                current = levels.get(edge.target, -1)
                if candidate_level > current:
                    levels[edge.target] = candidate_level
                    changed = True

        unresolved = [node_id for node_id in graph.nodes if node_id not in levels]
        fallback_level = (max(levels.values()) + 1) if levels else 0
        for offset, node_id in enumerate(unresolved):
            levels[node_id] = fallback_level + offset
        return levels


class WorkflowGraphRenderer:
    """Draw a workflow graph onto a FreeSimpleGUI Graph canvas."""

    def __init__(self, layout_engine: WorkflowLayoutEngine | None = None) -> None:
        self.layout_engine = layout_engine or WorkflowLayoutEngine()

    def render(
        self,
        graph_canvas: sg.Graph,
        workflow: Workflow,
        *,
        selected_node_id: str | None = None,
    ) -> dict[str, NodeBounds]:
        """Redraw the graph and return bounds keyed by node id."""

        graph_canvas.erase()
        positions = self.layout_engine.layout(workflow)
        bounds: dict[str, NodeBounds] = {}

        for edge in workflow.graph.edges:
            self._draw_edge(graph_canvas, workflow, positions, edge.source, edge.target)

        for node_id, node in workflow.graph.nodes.items():
            center = positions.get(node_id)
            if center is None:
                continue
            bounds[node_id] = self._draw_node(
                graph_canvas,
                node,
                center,
                selected=node_id == selected_node_id,
            )

        return bounds

    def _draw_edge(
        self,
        graph_canvas: sg.Graph,
        workflow: Workflow,
        positions: dict[str, tuple[float, float]],
        source_id: str,
        target_id: str,
    ) -> None:
        source_node = workflow.graph.nodes.get(source_id)
        target_node = workflow.graph.nodes.get(target_id)
        if source_node is None or target_node is None:
            return

        source_center = positions.get(source_id)
        target_center = positions.get(target_id)
        if source_center is None or target_center is None:
            return

        layout = self.layout_engine
        source_width, source_height = layout.node_size(source_node)
        target_width, target_height = layout.node_size(target_node)

        start_x, start_y = source_center[0], source_center[1] - source_height / 2
        end_x, end_y = target_center[0], target_center[1] + target_height / 2
        mid_y = (start_y + end_y) / 2

        graph_canvas.draw_line((start_x, start_y), (start_x, mid_y), color="#475569", width=2)
        graph_canvas.draw_line((start_x, mid_y), (end_x, mid_y), color="#475569", width=2)
        graph_canvas.draw_line((end_x, mid_y), (end_x, end_y), color="#475569", width=2)

        if target_width and source_width:
            arrow_y = end_y + 8
            graph_canvas.draw_polygon(
                [
                    (end_x - 6, arrow_y),
                    (end_x + 6, arrow_y),
                    (end_x, arrow_y - 10),
                ],
                fill_color="#475569",
                line_color="#475569",
            )

    def _draw_node(
        self,
        graph_canvas: sg.Graph,
        node: WorkflowNode,
        center: tuple[float, float],
        *,
        selected: bool,
    ) -> NodeBounds:
        layout = self.layout_engine
        width, height = layout.node_size(node)
        bounds = layout.node_bounds(node, center[0], center[1])

        fill_color = "#dbeafe" if node.kind == "branch" else "#f8fafc"
        line_color = "#1d4ed8" if selected else "#334155"
        text_color = "#0f172a"
        line_width = 4 if selected else 2

        if node.kind == "branch":
            graph_canvas.draw_polygon(
                [
                    (center[0], center[1] + height / 2),
                    (center[0] + width / 2, center[1]),
                    (center[0], center[1] - height / 2),
                    (center[0] - width / 2, center[1]),
                ],
                fill_color=fill_color,
                line_color=line_color,
                line_width=line_width,
            )
        else:
            graph_canvas.draw_rectangle(
                (center[0] - width / 2, center[1] + height / 2),
                (center[0] + width / 2, center[1] - height / 2),
                fill_color=fill_color,
                line_color=line_color,
                line_width=line_width,
            )

        graph_canvas.draw_text(
            node.widget.name,
            (center[0], center[1] + 10),
            color=text_color,
            font=("Arial", 13, "bold"),
        )
        test_count = len(node.widget.metadata.get("analyzer_tests", []))
        graph_canvas.draw_text(
            f"Tests: {test_count} | Staff: {node.widget.required_staff} | Capacity: {node.widget.capacity}",
            (center[0], center[1] - 16),
            color="#475569",
            font=("Arial", 10),
        )
        return bounds


class WorkflowEditorController:
    """Stateful controller for the workflow editor window."""

    def __init__(
        self,
        repository: WorkflowRepository | None = None,
        current_document: WorkflowDocument | None = None,
    ) -> None:
        self.repository = repository or WorkflowRepository()
        self.current_document: WorkflowDocument | None = current_document
        self.selected_node_id: str | None = None
        self.workflow_counter = 0
        self.bounds: dict[str, NodeBounds] = {}
        self.renderer = WorkflowGraphRenderer()

    def refresh_document_table(self, window: sg.Window) -> None:
        documents = self.repository.list_documents()
        window["-WF-DOCS-"].update(
            values=[
                [document.workflow_id, document.name, len(document.workflow.widgets), len(document.workflow.scenarios)]
                for document in documents
            ]
        )

    def refresh_summary(self, window: sg.Window) -> None:
        if self.current_document is None:
            window["-WF-NAME-"].update("No workflow loaded")
            window["-WF-SUMMARY-"].update("Create a workflow to begin.")
            window["-WF-STATUS-"].update("Status: No workflow", text_color="#64748b")
            window["-WF-SCENARIOS-"].update(values=[])
            window["-WF-WIDGETS-"].update(values=[])
            window["-WF-DETAILS-"].update("Select a node to edit its properties.")
            self.update_analyzer_tests_button(window)
            return

        workflow = self.current_document.workflow
        window["-WF-NAME-"].update(self.current_document.name)
        window["-WF-STATUS-"].update("Status: Saved", text_color="#15803d")
        window["-WF-SUMMARY-"].update(
            f"Nodes: {len(workflow.graph.nodes)} | Edges: {len(workflow.graph.edges)} | Scenarios: {len(workflow.scenarios)}"
        )
        window["-WF-SCENARIOS-"].update(
            values=[
                [scenario.name, scenario.description or "-", len(scenario.widget_service_times)]
                for scenario in workflow.scenarios
            ]
        )
        window["-WF-WIDGETS-"].update(
            values=[
                [
                    node.node_id,
                    node.widget.name,
                    node.kind,
                    node.widget.throughput,
                    len(workflow.graph.outgoing_edges(node.node_id)),
                ]
                for node in workflow.graph.iter_nodes()
            ]
        )
        self.refresh_selected_details(window)

    def refresh_selected_details(self, window: sg.Window) -> None:
        if self.current_document is None or self.selected_node_id is None:
            window["-WF-DETAILS-"].update("Select a node to edit its properties.")
            return

        node = self.current_document.workflow.get_node(self.selected_node_id)
        if node is None:
            window["-WF-DETAILS-"].update("Select a node to edit its properties.")
            return

        incoming = len(self.current_document.workflow.graph.incoming_edges(node.node_id))
        outgoing = len(self.current_document.workflow.graph.outgoing_edges(node.node_id))
        details = [
            f"Selected: {node.widget.name}",
            f"ID: {node.node_id}",
            f"Type: {node.widget.node_type}",
            f"Processing Capacity (per hours): {node.widget.throughput:g}",
            f"Human Processing Capacity (per hour): {node.widget.human_throughput:g}",
            f"Capacity: {node.widget.capacity}",
            f"Required staff: {node.widget.required_staff}",
            f"Incoming edges: {incoming}",
            f"Outgoing edges: {outgoing}",
        ]
        analyzer_tests = node.widget.metadata.get("analyzer_tests", [])
        if analyzer_tests:
            details.append(f"Analyzer tests: {', '.join(str(test) for test in analyzer_tests)}")
        if node.metadata:
            details.append(f"Metadata: {node.metadata}")
        window["-WF-DETAILS-"].update("\n".join(details))
        self.update_analyzer_tests_button(window)

    def update_analyzer_tests_button(self, window: sg.Window) -> None:
        """Enable the test selection button only for analyzer nodes."""

        button = window["-WF-SELECT-TESTS-"]
        node = self.selected_node()
        is_analyzer = bool(node and node.widget.node_type in {"automated analyzer", "manual analyzer"})
        button.update(disabled=not is_analyzer)

    def rerender(self, window: sg.Window) -> None:
        if self.current_document is None:
            self.bounds = {}
            window["-WF-GRAPH-"].erase()
            window.refresh()
            return
        self.bounds = self.renderer.render(
            window["-WF-GRAPH-"],
            self.current_document.workflow,
            selected_node_id=self.selected_node_id,
        )
        window.refresh()

    def refresh(self, window: sg.Window) -> None:
        self.refresh_document_table(window)
        self.refresh_summary(window)
        self.rerender(window)
        self.update_graph_context_menu(window)

    def update_graph_context_menu(self, window: sg.Window) -> None:
        """Refresh the graph's right-click menu for the current workflow."""

        graph = window["-WF-GRAPH-"]
        graph.set_right_click_menu(self.build_graph_context_menu())

    def build_graph_context_menu(self) -> list[Any] | None:
        """Build a right-click menu for adding or removing connections."""

        if self.current_document is None:
            return [
                "Workflow Node",
                [
                    "Add connection",
                    ["No workflow loaded::noop"],
                    "Disconnect from",
                    ["No workflow loaded::noop"],
                ],
            ]

        nodes = self.current_document.workflow.graph.iter_nodes()
        selected_node = self.selected_node()
        target_items = []
        for node in nodes:
            if node.node_id == self.selected_node_id:
                continue
            if not self._is_valid_connection_target(node.node_id):
                continue
            target_items.append(f"Add connection to {node.widget.name}::{node.node_id}")
        outgoing_ids = {
            edge.target
            for edge in self.current_document.workflow.graph.outgoing_edges(self.selected_node_id or "")
        }
        disconnect_items = [
            f"Disconnect from {node.widget.name}::{node.node_id}"
            for node in nodes
            if node.node_id in outgoing_ids
        ]
        if not target_items:
            target_items = ["No other nodes::noop"]
        if not disconnect_items:
            disconnect_items = ["No connections::noop"]
        menu = ["Workflow Node", ["Add connection", target_items, "Disconnect from", disconnect_items]]
        if selected_node is not None and selected_node.widget.node_type in {
            "automated analyzer",
            "manual analyzer",
        }:
            menu.append("Select tests::select_analyzer_tests")
        return menu

    def _is_valid_connection_target(self, target_node_id: str) -> bool:
        """Return whether the current selection can connect to ``target_node_id``.

        The editor keeps the submenu focused on legal targets: the selected node
        itself is excluded, and we avoid targets that would introduce a cycle by
        connecting back into the currently selected node.
        """

        if self.current_document is None or self.selected_node_id is None:
            return True
        if target_node_id == self.selected_node_id:
            return False

        workflow = self.current_document.workflow
        seen: set[str] = set()
        stack = [target_node_id]
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            if node_id == self.selected_node_id:
                return False
            for edge in workflow.graph.outgoing_edges(node_id):
                if edge.target not in seen:
                    stack.append(edge.target)
        return True

    def persist_current_workflow(self, window: sg.Window | None = None) -> str:
        """Write the current workflow document to disk."""

        if self.current_document is None:
            raise ValueError("No workflow is loaded.")
        if window is not None:
            window["-WF-STATUS-"].update("Status: Saving...", text_color="#b45309")
            window.refresh()
        saved_path = self.repository.save(self.current_document)
        if window is not None:
            window["-WF-STATUS-"].update("Status: Saved", text_color="#15803d")
            window.refresh()
        return str(saved_path)

    def create_new_workflow(self, window: sg.Window | None = None) -> str:
        self.workflow_counter += 1
        workflow_name = f"Workflow {self.workflow_counter}"
        self.current_document = WorkflowDocument.create(name=workflow_name)
        self.selected_node_id = None
        logger.info("CREATED NEW WORKFLOW: %s", workflow_name)
        self.persist_current_workflow(window)
        return workflow_name

    def add_node(self, window: sg.Window | None = None) -> str:
        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        widget = self.current_document.workflow.graph.create_default_widget()
        node = self.current_document.workflow.graph.add_widget(widget)
        self.selected_node_id = node.node_id
        self.persist_current_workflow(window)
        return node.node_id

    def save_current_workflow(self, window: sg.Window | None = None) -> str:
        if self.current_document is None:
            raise ValueError("No workflow is loaded.")
        saved_path = self.persist_current_workflow(window)
        logger.info("SAVED WORKFLOW: %s", self.current_document.name)
        return saved_path

    def load_workflow_by_row(self, row_index: int) -> None:
        documents = self.repository.list_documents()
        if row_index < 0 or row_index >= len(documents):
            raise IndexError(row_index)
        self.current_document = self.repository.load(documents[row_index].workflow_id)
        self.selected_node_id = None
        logger.info("LOADED WORKFLOW: %s", self.current_document.name)

    def add_scenario(self, window: sg.Window | None = None) -> str:
        if self.current_document is None:
            raise ValueError("No workflow is loaded.")
        scenario_name = f"Scenario {len(self.current_document.workflow.scenarios) + 1}"
        scenario = Scenario(name=scenario_name, description="Alternative run configuration")
        self.current_document.workflow.add_scenario(scenario)
        self.persist_current_workflow(window)
        return scenario_name

    def node_at(self, x: float, y: float) -> str | None:
        for node_id, bounds in self.bounds.items():
            if bounds.contains(x, y):
                return node_id
        return None

    def select_node_at(self, x: float, y: float) -> str | None:
        self.selected_node_id = self.node_at(x, y)
        return self.selected_node_id

    def selected_node(self) -> WorkflowNode | None:
        if self.current_document is None or self.selected_node_id is None:
            return None
        return self.current_document.workflow.get_node(self.selected_node_id)

    def update_node_from_form(
        self,
        node_id: str,
        *,
        node_type: str,
        name: str,
        description: str,
        throughput: float,
        human_throughput: float,
        capacity: int,
        required_staff: int,
        window: sg.Window | None = None,
    ) -> None:
        """Apply form values to a node and persist the workflow."""

        node = self.current_document.workflow.get_node(node_id) if self.current_document else None
        if node is None:
            raise ValueError("Selected node is no longer available.")

        self.current_document.workflow.update_node_properties(
            node_id,
            name=name,
            service_time=throughput,
            metadata={
                "node_type": node_type,
                "throughput": throughput,
                "human_throughput": human_throughput,
                "description": description,
                "required_staff": required_staff,
                "capacity": capacity,
            },
            kind=node_type,
        )
        node.widget.node_type = node_type
        node.widget.throughput = throughput
        node.widget.human_throughput = human_throughput
        node.widget.capacity = capacity
        node.widget.description = description
        node.widget.required_staff = required_staff
        self.persist_current_workflow(window)

    def connect_selected_node_to(self, target_node_id: str, window: sg.Window | None = None) -> None:
        """Add a connection from the selected node to another node."""

        if target_node_id == "noop":
            raise ValueError("No other nodes are available to connect to.")
        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        if self.selected_node_id is None:
            raise ValueError("Select a node first.")
        if self.selected_node_id == target_node_id:
            raise ValueError("A node cannot connect to itself.")

        source_node = self.current_document.workflow.get_node(self.selected_node_id)
        target_node = self.current_document.workflow.get_node(target_node_id)
        if source_node is None or target_node is None:
            raise ValueError("Selected nodes are no longer available.")

        self.current_document.workflow.graph.connect_nodes(source_node.node_id, target_node.node_id)
        self.persist_current_workflow(window)

    def disconnect_selected_node_from(self, target_node_id: str, window: sg.Window | None = None) -> None:
        """Remove a connection from the selected node to another node."""

        if target_node_id == "noop":
            raise ValueError("No connections are available to remove.")
        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        if self.selected_node_id is None:
            raise ValueError("Select a node first.")
        if self.selected_node_id == target_node_id:
            raise ValueError("A node cannot disconnect from itself.")

        source_node = self.current_document.workflow.get_node(self.selected_node_id)
        target_node = self.current_document.workflow.get_node(target_node_id)
        if source_node is None or target_node is None:
            raise ValueError("Selected nodes are no longer available.")

        self.current_document.workflow.graph.disconnect_nodes(source_node.node_id, target_node.node_id)
        self.persist_current_workflow(window)

    def _next_node_name(self, suffix: str) -> str:
        if self.current_document is None:
            return f"Node {self.workflow_counter + 1}"
        return f"Node {len(self.current_document.workflow.graph.nodes) + 1}"

    def add_connected_node(self, window: sg.Window | None = None) -> str:
        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        if self.selected_node_id is None:
            raise ValueError("Select a node first.")
        node = self.current_document.workflow.create_connected_node(
            self.selected_node_id,
            self._next_node_name("Step"),
            0.0,
        )
        self.selected_node_id = node.node_id
        self.persist_current_workflow(window)
        return node.node_id

    def add_branch_node(self, window: sg.Window | None = None) -> str:
        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        if self.selected_node_id is None:
            raise ValueError("Select a node first.")
        node = self.current_document.workflow.create_branch_node(
            self.selected_node_id,
            self._next_node_name("Branch"),
        )
        self.selected_node_id = node.node_id
        self.persist_current_workflow(window)
        return node.node_id

    def rename_selected_node(self, new_name: str, window: sg.Window | None = None) -> None:
        node = self.selected_node()
        if node is None:
            raise ValueError("Select a node first.")
        self.current_document.workflow.rename_node(node.node_id, new_name)
        self.persist_current_workflow(window)

    def update_selected_node(
        self,
        *,
        name: str,
        service_time: float,
        kind: str,
        description: str,
        capacity: int = 1,
        window: sg.Window | None = None,
    ) -> None:
        node = self.selected_node()
        if node is None:
            raise ValueError("Select a node first.")
        metadata: dict[str, Any] = {"description": description}
        self.current_document.workflow.update_node_properties(
            node.node_id,
            name=name,
            service_time=service_time,
            metadata={**metadata, "capacity": capacity},
            kind=kind,
        )
        node.widget.capacity = capacity
        self.persist_current_workflow(window)

    def delete_selected_node(self, window: sg.Window | None = None) -> None:
        node = self.selected_node()
        if node is None:
            raise ValueError("Select a node first.")
        self.current_document.workflow.delete_node(node.node_id)
        self.selected_node_id = None
        self.persist_current_workflow(window)

    def set_analyzer_tests(
        self,
        node_id: str,
        test_codes: list[str],
        window: sg.Window | None = None,
    ) -> None:
        """Store the analyzer test selection on the selected node."""

        node = self.current_document.workflow.get_node(node_id) if self.current_document else None
        if node is None:
            raise ValueError("Selected node is no longer available.")

        self.current_document.workflow.update_node_properties(
            node_id,
            metadata={"analyzer_tests": list(test_codes)},
        )
        node.widget.metadata["analyzer_tests"] = list(test_codes)
        self.persist_current_workflow(window)


def _show_node_properties_dialog(node: WorkflowNode | None) -> dict[str, Any] | None:
    """Prompt for editable node properties."""

    if node is None:
        return None

    layout = [
        [sg.Text("Name"), sg.Input(node.widget.name, key="-NAME-", expand_x=True)],
        [
            sg.Text("Processing Capacity (per hours)"),
            sg.Input(str(node.widget.service_time), key="-SERVICE-", size=(12, 1)),
        ],
        [
            sg.Text("Capacity"),
            sg.Input(str(node.widget.capacity), key="-CAPACITY-", size=(12, 1)),
        ],
        [
            sg.Text("Type"),
            sg.Combo(["task", "branch"], default_value=node.kind, key="-KIND-", readonly=True, size=(12, 1)),
        ],
        [
            sg.Text("Description"),
            sg.Multiline(node.metadata.get("description", ""), key="-DESC-", size=(42, 4), expand_x=True),
        ],
        [sg.Button("Save", key="-SAVE-", bind_return_key=True), sg.Button("Cancel")],
    ]
    window = sg.Window("Node Properties", layout=layout, modal=True, finalize=True, keep_on_top=True)
    try:
        while True:
            event, values = window.read()
            if event in ("Cancel", sg.WIN_CLOSED):
                return None
            if event == "-SAVE-":
                try:
                    service_time = float(values["-SERVICE-"])
                    capacity = int(values["-CAPACITY-"])
                except (TypeError, ValueError):
                    _notify("Processing capacity must be a number and capacity must be an integer.")
                    continue
                name = str(values["-NAME-"]).strip() or "Unnamed Node"
                description = str(values["-DESC-"]).strip()
                kind = str(values["-KIND-"])
                return {
                    "name": name,
                    "service_time": service_time,
                    "capacity": capacity,
                    "kind": kind,
                    "description": description,
                }
    finally:
        window.close()


def open_workflow_editor_window(
    current_document: WorkflowDocument | None = None,
    repository: WorkflowRepository | None = None,
) -> None:
    """Open the interactive workflow editor window."""

    controller = WorkflowEditorController(repository=repository, current_document=current_document)

    layout = [
        [sg.Text("Workflow Editor", font=("Arial", 20, "bold"))],
        [
            sg.Text("Current workflow:"),
            sg.Text("No workflow loaded", key="-WF-NAME-"),
            sg.Text("Status: No workflow", key="-WF-STATUS-", text_color="#64748b"),
            sg.Text("Select a node and use the toolbar to edit the graph.", text_color="#475569"),
        ],
        [sg.Text(f"Storage: {controller.repository.base_dir}", key="-WF-STORAGE-", size=(90, 1))],
        [sg.Text("Create a workflow to begin.", key="-WF-SUMMARY-")],
        [
            sg.Button("New Workflow", key="-WF-NEW-", button_color=("white", "green")),
            sg.Button("Load Selected", key="-WF-LOAD-"),
            sg.Button("Save Current", key="-WF-SAVE-"),
            sg.Button("Add Node", key="-WF-ADD-NODE-"),
            sg.Button("Add Connected Node", key="-WF-ADD-CONNECTED-"),
            sg.Button("Add Branch Node", key="-WF-ADD-BRANCH-"),
            sg.Button("Rename Node", key="-WF-RENAME-"),
            sg.Button("Edit Properties", key="-WF-PROPS-"),
            sg.Button("Select tests", key="-WF-SELECT-TESTS-", disabled=True),
            sg.Button("Delete Node", key="-WF-DELETE-", button_color=("white", "#b91c1c")),
            sg.Button("Add Scenario", key="-WF-ADD-SCENARIO-"),
            sg.CloseButton("Close"),
        ],
        [
            sg.Column(
                [
                    [sg.Text("Graph", font=("Arial", 14, "bold"))],
                    [
                        sg.Graph(
                            canvas_size=(2400, 1800),
                            graph_bottom_left=(0, 0),
                            graph_top_right=(2400, 1800),
                            key="-WF-GRAPH-",
                            enable_events=True,
                            background_color="#f8fafc",
                        )
                    ],
                ],
                key="-WF-GRAPH-SCROLL-",
                size=(980, 780),
                scrollable=True,
                vertical_scroll_only=False,
                expand_x=True,
                expand_y=True,
            ),
            sg.Column(
                [
                    [sg.Text("Selected node", font=("Arial", 14, "bold"))],
                    [
                        sg.Multiline(
                            "Select a node to edit its properties.",
                            key="-WF-DETAILS-",
                            size=(42, 14),
                            disabled=True,
                            expand_x=True,
                            expand_y=False,
                        )
                    ],
                    [sg.Text("Saved workflows", font=("Arial", 14, "bold"))],
                    [
                        sg.Table(
                            values=[],
                            headings=["ID", "Name", "Nodes", "Scenarios"],
                            key="-WF-DOCS-",
                            auto_size_columns=True,
                            expand_x=True,
                            num_rows=6,
                            alternating_row_color="lightyellow",
                            font=("Arial", 11),
                            enable_events=True,
                        )
                    ],
                    [sg.Text("Scenarios", font=("Arial", 14, "bold"))],
                    [
                        sg.Table(
                            values=[],
                            headings=["Scenario", "Description", "Overrides"],
                            key="-WF-SCENARIOS-",
                            auto_size_columns=True,
                            expand_x=True,
                            num_rows=4,
                            alternating_row_color="lightgreen",
                            font=("Arial", 11),
                        )
                    ],
                    [sg.Text("Workflow nodes", font=("Arial", 14, "bold"))],
                    [
                        sg.Table(
                            values=[],
                            headings=["Node", "Name", "Type", "Service", "Outputs"],
                            key="-WF-WIDGETS-",
                            auto_size_columns=True,
                            expand_x=True,
                            expand_y=True,
                            num_rows=10,
                            alternating_row_color="lightblue",
                            font=("Arial", 11),
                        )
                    ],
                ],
                size=(440, 780),
                expand_x=True,
                expand_y=True,
            ),
        ],
    ]

    window = sg.Window(
        "Workflow Editor",
        layout=layout,
        modal=False,
        resizable=True,
        finalize=True,
        keep_on_top=True,
        size=(1460, 940),
    )

    _bind_graph_right_click_selection(window["-WF-GRAPH-"], controller, window)
    _bind_graph_double_click_properties(window["-WF-GRAPH-"], controller, window)
    controller.refresh(window)
    _reset_scrollable_view(window["-WF-GRAPH-SCROLL-"])
    window.refresh()

    try:
        while True:
            event, values = window.read()
            if event in ("Exit", sg.WIN_CLOSED):
                break
            if event == "-WF-GRAPH-":
                click = values.get("-WF-GRAPH-")
                if isinstance(click, tuple) and len(click) == 2:
                    controller.select_node_at(float(click[0]), float(click[1]))
                    controller.refresh_summary(window)
                    controller.rerender(window)
                continue
            if event == "-WF-GRAPH-DOUBLE-":
                controller.refresh_summary(window)
                controller.rerender(window)
                node = controller.selected_node()
                result = _show_node_form_dialog(node)
                if result is not None and node is not None:
                    controller.update_node_from_form(node.node_id, window=window, **result)
                    controller.refresh(window)
                continue

            try:
                if isinstance(event, str) and "::" in event:
                    _, target_node_id = event.rsplit("::", 1)
                    if event.endswith("::select_analyzer_tests"):
                        node = controller.selected_node()
                        if node is None:
                            _notify("Select a node first.")
                            continue
                        if node.widget.node_type not in {"automated analyzer", "manual analyzer"}:
                            _notify("Select an automated analyzer or manual analyzer node first.")
                            continue
                        available_tests = _load_available_tests()
                        selected_codes = _show_test_selection_dialog(
                            node=node,
                            available_tests=available_tests,
                        )
                        if selected_codes is not None:
                            controller.set_analyzer_tests(node.node_id, selected_codes, window)
                            controller.refresh(window)
                            _notify("Analyzer tests updated.")
                    elif event.startswith("Add connection to "):
                        controller.connect_selected_node_to(target_node_id, window)
                        controller.refresh(window)
                        _notify("Connection added.")
                    elif event.startswith("Disconnect from "):
                        controller.disconnect_selected_node_from(target_node_id, window)
                        controller.refresh(window)
                        _notify("Connection removed.")
                    else:
                        _notify("Unsupported workflow action.")
                    continue
                if event == "-WF-SELECT-TESTS-":
                    node = controller.selected_node()
                    if node is None:
                        _notify("Select an analyzer node first.")
                        continue
                    if node.widget.node_type not in {"automated analyzer", "manual analyzer"}:
                        _notify("Select an analyzer node first.")
                        continue
                    available_tests = _load_available_tests()
                    selected_codes = _show_test_selection_dialog(
                        node=node,
                        available_tests=available_tests,
                    )
                    if selected_codes is not None:
                        controller.set_analyzer_tests(node.node_id, selected_codes, window)
                        controller.refresh(window)
                        _notify("Analyzer tests updated.")
                    continue
                if event == "-WF-NEW-":
                    workflow_name = controller.create_new_workflow(window)
                    controller.refresh(window)
                    _notify(f"{workflow_name} created.")
                elif event == "-WF-SAVE-":
                    if controller.current_document is None:
                        _notify("Create or load a workflow before saving.")
                        continue
                    saved_path = controller.save_current_workflow(window)
                    controller.refresh(window)
                    _notify(f"{controller.current_document.name} saved to {saved_path}.")
                elif event == "-WF-LOAD-" and values["-WF-DOCS-"]:
                    selected_row = values["-WF-DOCS-"][0]
                    controller.load_workflow_by_row(selected_row)
                    controller.refresh(window)
                    _reset_scrollable_view(window["-WF-GRAPH-SCROLL-"])
                elif event == "-WF-ADD-SCENARIO-":
                    scenario_name = controller.add_scenario(window)
                    controller.refresh_summary(window)
                    _notify(f"{scenario_name} added.")
                elif event == "-WF-ADD-NODE-":
                    controller.add_node(window)
                    controller.refresh(window)
                elif event == "-WF-ADD-CONNECTED-":
                    controller.add_connected_node(window)
                    controller.refresh(window)
                elif event == "-WF-ADD-BRANCH-":
                    controller.add_branch_node(window)
                    controller.refresh(window)
                elif event == "-WF-RENAME-":
                    node = controller.selected_node()
                    if node is None:
                        _notify("Select a node first.")
                        continue
                    new_name = sg.popup_get_text("Rename node", default_text=node.widget.name, title="Workflow")
                    if new_name:
                        controller.rename_selected_node(new_name.strip(), window)
                        controller.refresh(window)
                elif event == "-WF-PROPS-":
                    node = controller.selected_node()
                    result = _show_node_properties_dialog(node)
                    if result is not None:
                        controller.update_selected_node(**result, window=window)
                        controller.refresh(window)
                elif event == "-WF-DELETE-":
                    controller.delete_selected_node(window)
                    controller.refresh(window)
            except (ValueError, KeyError, IndexError) as exc:
                _notify(str(exc))
    finally:
        window.close()


def create_workflow_window(
    current_document: WorkflowDocument | None = None,
    repository: WorkflowRepository | None = None,
) -> None:
    """Backward-compatible alias for opening the editor window."""

    open_workflow_editor_window(current_document=current_document, repository=repository)
