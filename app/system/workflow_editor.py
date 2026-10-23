"""Workflow editor UI for graph-based workflow documents."""

from __future__ import annotations

import logging
import math
import tkinter as tk
from dataclasses import dataclass
from typing import Any, Callable

import FreeSimpleGUI as sg

from app.auth.windows import login_required
from app.ui_styles import create_window, DANGER_BUTTON_COLOR, bring_window_to_front
from app.project import ProjectEquipmentInstance
from app.system.floor_view import EQUIPMENT_CATALOG, EquipmentMetadata
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.system.models import Test, engine
from app.system.model import Scenario, Workflow, WorkflowNode
from app.system.scenario_editor import scenario_settings
from app.system.order_adapter import ADAPTER_TYPES
from app.system.staff_batching import DEFAULT_BATCH_SIZE, DEFAULT_BATCH_WAIT_MINUTES, validate_batch_settings
from app.system.storage import WorkflowDocument, WorkflowRepository


logger = logging.getLogger(__name__)

LEGACY_NODE_TYPE_ALIASES = {
    "AI agent": "Evaluation",
}


@dataclass(frozen=True, slots=True)
class EquipmentNodeResolution:
    """Display-time result of resolving a workflow equipment reference."""

    equipment_id: str
    display_name: str
    resolved: bool


def _notify(message: str) -> None:
    """Show a lightweight non-blocking workflow notification."""

    sg.popup_non_blocking(
        message,
        title="Workflow",
        background_color="lightyellow",
        keep_on_top=True,
    )


def _normalize_node_type_label(node_type: str) -> str:
    """Map legacy node type labels to their current display names."""

    return LEGACY_NODE_TYPE_ALIASES.get(node_type, node_type)


def _center_scroll_fraction(target: float, viewport: float, start: float, end: float) -> float:
    """Return a Tk canvas scroll fraction that centers a content coordinate."""

    extent = end - start
    if extent <= viewport or extent <= 0:
        return 0.0
    return max(0.0, min((target - viewport / 2 - start) / extent, 1.0 - viewport / extent))


def _scroll_canvas_for_column(column: sg.Column) -> Any | None:
    """Find the actual Tk canvas across FreeSimpleGUI scroll-column variants."""

    frame = getattr(column, "TKColFrame", None)
    if frame is not None and getattr(frame, "canvas", None) is not None:
        return frame.canvas
    widget = getattr(column, "Widget", None)
    if widget is None:
        return None
    return getattr(widget, "canvas", widget)


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
        if selected_node_id is None:
            edge = controller.edge_at(graph_x, graph_y)
            if edge is not None:
                previous_menu = getattr(graph, "_edge_context_menu", None)
                if previous_menu is not None:
                    previous_menu.destroy()
                menu = tk.Menu(graph.Widget, tearoff=False)
                graph._edge_context_menu = menu
                menu.add_command(
                    label="Remove Edge",
                    command=lambda: window.write_event_value("-WF-REMOVE-EDGE-", edge),
                )
                try:
                    menu.tk_popup(event.x_root, event.y_root)
                finally:
                    menu.grab_release()
                return "break"
        if selected_node_id is not None:
            controller.refresh_summary(window)
            controller.rerender(window)
            controller.update_graph_context_menu(window, selected_node_id=selected_node_id)

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


def _show_node_form_dialog(
    node: WorkflowNode | None,
    equipment_choices: list[tuple[str, str]] | None = None,
    configure_staff: Callable[[WorkflowNode], None] | None = None,
    active_scenario_name: str = "",
) -> dict[str, Any] | None:
    """Prompt for node configuration values."""

    if node is None:
        return None

    attendance_choices = {"Automatic": ""}
    attendance_choices.update({label: equipment_id for equipment_id, label in (equipment_choices or [])})
    current_attendance = str(node.metadata.get("attend_equipment_id", ""))
    if current_attendance and current_attendance not in attendance_choices.values():
        attendance_choices[f"{current_attendance} — Missing Equipment"] = current_attendance
    attendance_label = next(label for label, value in attendance_choices.items() if value == current_attendance)
    node_types = [
        "specimen registration",
        "specimens processing",
        "automated analyzer",
        "manual analyzer",
        "result reporting",
        "result validating",
        "Evaluation",
        "order adapter",
    ]
    current_type = _normalize_node_type_label(node.widget.node_type)
    if current_type not in node_types:
        current_type = node.kind
    layout = [
        [
            sg.Text("Node type"),
            sg.Combo(
                node_types,
                default_value=current_type,
                key="-NODE-TYPE-",
                readonly=True,
                expand_x=True,
                enable_events=True,
            ),
        ],
        [sg.Text("Name"), sg.Input(node.widget.name, key="-NAME-", expand_x=True)],
        [sg.Column([
            [sg.Text("Shared node settings — all scenarios", text_color="#64748b")],
            [sg.Text("Processing Capacity (per hours)"), sg.Input(str(node.widget.throughput), key="-THROUGHPUT-", size=(14, 1), enable_events=True), sg.Text(_capacity_time_label(node.widget.throughput), key="-THROUGHPUT-TIME-", size=(29, 1), tooltip=_CAPACITY_TIME_TOOLTIP)],
            [sg.Text("Human Processing Capacity (per hour)"), sg.Input(str(node.widget.human_throughput), key="-HUMAN-THROUGHPUT-", size=(14, 1), enable_events=True), sg.Text(_capacity_time_label(node.widget.human_throughput), key="-HUMAN-THROUGHPUT-TIME-", size=(29, 1), tooltip=_CAPACITY_TIME_TOOLTIP)],
            [sg.Text("Capacity"), sg.Input(str(node.widget.capacity), key="-CAPACITY-", size=(14, 1))],
            [sg.Text("Required staff"), sg.Input(str(node.widget.required_staff), key="-STAFF-", size=(14, 1))],
            [sg.Button("Allowed staff", key="-NODE-ALLOWED-STAFF-", disabled=configure_staff is None),
             sg.Text(f"Scenario: {active_scenario_name}", text_color="#2563eb")],
            [sg.Text("Attend at"), sg.Combo(list(attendance_choices), default_value=attendance_label,
                                          key="-ATTEND-AT-", readonly=True, size=(38, 1))],
        ], key="-PROCESSING-FIELDS-", visible=current_type != "order adapter")],
        [sg.Frame("Staff batching", [
            [sg.Text("Maximum specimens per batch"), sg.Input(str(node.metadata.get("batch_size", DEFAULT_BATCH_SIZE)), key="-BATCH-SIZE-", size=(10, 1))],
            [sg.Text("Maximum collection wait (minutes)"), sg.Input(str(node.metadata.get("batch_wait_minutes", DEFAULT_BATCH_WAIT_MINUTES)), key="-BATCH-WAIT-", size=(10, 1))],
            [sg.Text("Set batch size to 1 for individual processing. Human capacity remains per specimen.")],
        ], key="-BATCH-FIELDS-", visible=current_type == "specimens processing")],
        [sg.Column([
            [sg.Text("Adapter type"), sg.Combo(list(ADAPTER_TYPES), default_value=str(node.metadata.get("adapter_type", "simulation")), key="-ADAPTER-TYPE-", readonly=True, size=(20, 1))],
        ], key="-ADAPTER-FIELDS-", visible=current_type == "order adapter")],
        [
            sg.Text("Description"),
            sg.Multiline(node.widget.description, key="-DESC-", size=(46, 4), expand_x=True),
        ],
        [sg.Checkbox("Enabled", default=node.widget.enabled, key="-ENABLED-")],
        [sg.Button("Save", key="-SAVE-", bind_return_key=True), sg.Button("Cancel")],
    ]
    window = create_window("Node Properties", layout=layout, modal=True, finalize=True, keep_on_top=True)
    try:
        while True:
            event, values = window.read()
            if event in ("Cancel", sg.WIN_CLOSED):
                return None
            if event == "-NODE-ALLOWED-STAFF-" and configure_staff is not None:
                configure_staff(node)
                continue
            if event in ("-THROUGHPUT-", "-HUMAN-THROUGHPUT-"):
                window[f"{event}TIME-"].update(_capacity_time_label(values.get(event)))
                continue
            if event == "-NODE-TYPE-":
                is_adapter = values.get("-NODE-TYPE-") == "order adapter"
                window["-PROCESSING-FIELDS-"].update(visible=not is_adapter)
                window["-ADAPTER-FIELDS-"].update(visible=is_adapter)
                window["-BATCH-FIELDS-"].update(visible=values.get("-NODE-TYPE-") == "specimens processing")
                continue
            if event == "-SAVE-":
                name = str(values["-NAME-"]).strip() or "Unnamed Node"
                description = str(values["-DESC-"]).strip()
                node_type = str(values["-NODE-TYPE-"])
                if node_type == "order adapter":
                    return {
                        "node_type": node_type,
                        "name": name,
                        "description": description,
                        "adapter_type": str(values["-ADAPTER-TYPE-"]),
                        "enabled": bool(values.get("-ENABLED-", False)),
                    }
                try:
                    throughput = float(values["-THROUGHPUT-"])
                    human_throughput = float(values["-HUMAN-THROUGHPUT-"])
                    capacity = int(values["-CAPACITY-"])
                    required_staff = int(values["-STAFF-"])
                    batch_size, batch_wait_minutes = validate_batch_settings(
                        int(values["-BATCH-SIZE-"]), float(values["-BATCH-WAIT-"]))
                except (TypeError, ValueError):
                    _notify(
                        "Capacities and batch wait must be numeric; capacity, staff and batch size must be integers. Batch size must be positive and batch wait non-negative."
                    )
                    continue

                return {
                    "node_type": node_type,
                    "name": name,
                    "description": description,
                    "throughput": throughput,
                    "human_throughput": human_throughput,
                    "capacity": capacity,
                    "required_staff": required_staff,
                    "batch_size": batch_size,
                    "batch_wait_minutes": batch_wait_minutes,
                    "attend_equipment_id": attendance_choices.get(values.get("-ATTEND-AT-"), ""),
                    "enabled": bool(values.get("-ENABLED-", False)),
                }
    finally:
        window.close()


RESOURCE_ADAPTERS = {"patient": {"generate_per_run": "Generate per run"}}


def _show_resource_dialog(node: WorkflowNode | None = None) -> tuple[str, str, bool] | None:
    """Select a resource type, then one of its implemented adapters."""

    resource_labels = {resource.title(): resource for resource in RESOURCE_ADAPTERS}
    initial_type = str(node.metadata.get("resource_type", "patient")) if node else "patient"
    initial_label = initial_type.title()
    adapter_labels = {label: key for key, label in RESOURCE_ADAPTERS[initial_type].items()}
    initial_adapter = str(node.metadata.get("adapter_type", "generate_per_run")) if node else "generate_per_run"
    initial_adapter_label = RESOURCE_ADAPTERS[initial_type].get(initial_adapter, "")
    layout = [
        [sg.Text("Resource"), sg.Combo(list(resource_labels), default_value=initial_label,
                                        key="-RESOURCE-TYPE-", readonly=True, enable_events=True, disabled=node is not None)],
        [sg.Text("Adapter"), sg.Combo(list(adapter_labels), default_value=initial_adapter_label,
                                       key="-RESOURCE-ADAPTER-", readonly=True, size=(24, 1))],
        [sg.Checkbox("Enabled", default=node.widget.enabled if node else True, key="-RESOURCE-ENABLED-")],
        [sg.Button("Save"), sg.Button("Cancel")],
    ]
    dialog = create_window("Resource Settings", layout, modal=True, finalize=True, keep_on_top=True)
    try:
        while True:
            event, values = dialog.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                return None
            if event == "-RESOURCE-TYPE-":
                resource_type = resource_labels[str(values["-RESOURCE-TYPE-"])]
                choices = list(RESOURCE_ADAPTERS[resource_type].values())
                dialog["-RESOURCE-ADAPTER-"].update(values=choices, value=choices[0])
            if event == "Save":
                resource_type = resource_labels.get(str(values.get("-RESOURCE-TYPE-", "")))
                if resource_type is None:
                    _notify("Select a resource type.")
                    continue
                adapter_type = {label: key for key, label in RESOURCE_ADAPTERS[resource_type].items()}.get(
                    str(values.get("-RESOURCE-ADAPTER-", ""))
                )
                if adapter_type is None:
                    _notify("Select an adapter.")
                    continue
                return resource_type, adapter_type, bool(values.get("-RESOURCE-ENABLED-", False))
    finally:
        dialog.close()


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
    window = create_window(
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

    def layout(self, workflow: Workflow, *, use_stored_positions: bool = True) -> dict[str, tuple[float, float]]:
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
        self.canvas_height = max(self.canvas_height, 2 * self.top_margin + max_level * self.row_gap)
        self.canvas_width = max(self.canvas_width,
            2 * self.left_margin + max(self.node_width, self.branch_size)
            + (max(map(len, grouped.values())) - 1) * self.column_gap)
        for level in sorted(grouped):
            node_ids = sorted(
                grouped[level],
                key=lambda node_id: graph.nodes[node_id].widget.name.lower(),
            )
            total_width = max(0, (len(node_ids) - 1) * self.column_gap)
            start_x = max(self.left_margin, (self.canvas_width - total_width) / 2)
            y = self.canvas_height - self.top_margin - level * self.row_gap
            for index, node_id in enumerate(node_ids):
                node = graph.nodes[node_id]
                stored_position = node.metadata.get("editor_position")
                if (
                    use_stored_positions
                    and isinstance(stored_position, (list, tuple))
                    and len(stored_position) == 2
                    and all(isinstance(value, (int, float)) for value in stored_position)
                ):
                    positions[node_id] = (float(stored_position[0]), float(stored_position[1]))
                elif use_stored_positions and isinstance(node.metadata.get("x"), (int, float)) and isinstance(
                    node.metadata.get("y"), (int, float)
                ):
                    # Keep compatibility with early editor metadata while using
                    # editor_position as the canonical project field.
                    positions[node_id] = (
                        float(node.metadata["x"]),
                        float(node.metadata["y"]),
                    )
                else:
                    positions[node_id] = (start_x + index * self.column_gap, y)
        if use_stored_positions:
            bounds = [self.node_bounds(graph.nodes[node_id], *center)
                      for node_id, center in positions.items()]
            overlaps = any(a.left < b.right + 20 and a.right + 20 > b.left
                           and a.bottom < b.top + 20 and a.top + 20 > b.bottom
                           for index, a in enumerate(bounds) for b in bounds[index + 1:])
            if overlaps or any(b.left < 0 or b.bottom < 0 for b in bounds):
                return self.layout(workflow, use_stored_positions=False)
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

    def __init__(
        self,
        layout_engine: WorkflowLayoutEngine | None = None,
        equipment_resolver: Callable[[WorkflowNode], EquipmentNodeResolution] | None = None,
    ) -> None:
        self.layout_engine = layout_engine or WorkflowLayoutEngine()
        self.equipment_resolver = equipment_resolver

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
        layout = self.layout_engine
        extents = [layout.node_bounds(workflow.graph.nodes[node_id], *center)
                   for node_id, center in positions.items()]
        canvas_size = (
            max(2400, layout.canvas_width,
                int(max((bounds.right for bounds in extents), default=0) + layout.left_margin)),
            max(1800, layout.canvas_height,
                int(max((bounds.top for bounds in extents), default=0) + layout.top_margin)),
        )
        graph_canvas.set_size(canvas_size)
        graph_canvas.change_coordinates((0, 0), canvas_size)
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

        enabled = node.widget.enabled
        fill_color = "#dbeafe" if node.kind == "branch" else "#f8fafc"
        equipment_resolution = (
            self.equipment_resolver(node)
            if node.kind == "equipment" and self.equipment_resolver is not None
            else None
        )
        if equipment_resolution is not None and not equipment_resolution.resolved:
            fill_color = "#fee2e2"
        if not enabled:
            fill_color = "#e5e7eb"
        line_color = "#b91c1c" if equipment_resolution is not None and not equipment_resolution.resolved else "#334155"
        if selected:
            line_color = "#1d4ed8"
        elif not enabled:
            line_color = "#6b7280"
        text_color = "#0f172a" if enabled else "#4b5563"
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

        if not enabled:
            badge_x = center[0] + width / 2 - 16
            badge_y = center[1] + height / 2 - 16
            graph_canvas.draw_circle(
                (badge_x, badge_y),
                10,
                fill_color="#dc2626",
                line_color="#991b1b",
                line_width=1,
            )
            graph_canvas.draw_text(
                "×",
                (badge_x, badge_y - 1),
                color="white",
                font=("Arial", 11, "bold"),
            )

        display_name = equipment_resolution.display_name if equipment_resolution is not None else node.widget.name
        graph_canvas.draw_text(
            display_name,
            (center[0], center[1] + 10),
            color=text_color,
            font=("Arial", 13, "bold"),
        )
        if equipment_resolution is not None:
            label_parts = [equipment_resolution.equipment_id or "No equipment selected"]
            if not equipment_resolution.resolved:
                label_parts.append("Unresolved")
        elif node.widget.node_type == "order adapter":
            label_parts = [f"Source: {node.metadata.get('adapter_type', 'simulation')}"]
        else:
            test_count = len(node.widget.metadata.get("analyzer_tests", []))
            label_parts = [f"Tests: {test_count}", f"Staff: {node.widget.required_staff}", f"Capacity: {node.widget.capacity}"]
        status_label = "Disabled" if not enabled else ""
        if status_label:
            label_parts.append(status_label)
        graph_canvas.draw_text(
            " | ".join(label_parts),
            (center[0], center[1] - 16),
            color="#6b7280" if not enabled else "#475569",
            font=("Arial", 10),
        )
        return bounds


class WorkflowEditorController:
    """Stateful controller for the workflow editor window."""

    def __init__(
        self,
        repository: WorkflowRepository | None = None,
        current_document: WorkflowDocument | None = None,
        on_change: Callable[[], None] | None = None,
        equipment_instances: list[ProjectEquipmentInstance] | None = None,
        equipment_catalog: dict[str, EquipmentMetadata] | None = None,
    ) -> None:
        self.repository = repository or WorkflowRepository()
        self.current_document: WorkflowDocument | None = current_document
        self.on_change = on_change
        self.equipment_instances = equipment_instances if equipment_instances is not None else []
        self.equipment_catalog = equipment_catalog or EQUIPMENT_CATALOG
        self.selected_node_id: str | None = None
        self.workflow_counter = 0
        self.bounds: dict[str, NodeBounds] = {}
        self.renderer = WorkflowGraphRenderer(equipment_resolver=self.resolve_equipment_node)

    def equipment_instance(self, equipment_id: str) -> ProjectEquipmentInstance | None:
        return next(
            (instance for instance in self.equipment_instances if instance.instance_id == equipment_id),
            None,
        )

    def equipment_choices(self) -> list[tuple[str, str]]:
        """Return current project equipment IDs and display labels."""

        choices: list[tuple[str, str]] = []
        for instance in self.equipment_instances:
            metadata = self.equipment_catalog.get(instance.equipment_type)
            display_name = metadata.name if metadata is not None else instance.equipment_type
            choices.append((instance.instance_id, f"{instance.instance_id} — {display_name}"))
        return choices

    def resolve_equipment_node(self, node: WorkflowNode) -> EquipmentNodeResolution:
        equipment_id = str(node.metadata.get("equipment_id", ""))
        instance = self.equipment_instance(equipment_id)
        if instance is None:
            return EquipmentNodeResolution(equipment_id, "Missing Equipment", False)
        metadata = self.equipment_catalog.get(instance.equipment_type)
        display_name = metadata.name if metadata is not None else instance.equipment_type
        return EquipmentNodeResolution(equipment_id, display_name, True)

    def node_display_name(self, node: WorkflowNode) -> str:
        if node.kind == "equipment":
            return self.resolve_equipment_node(node).display_name
        return node.widget.name

    def is_reporting_station(self, equipment_id: str) -> bool:
        instance = self.equipment_instance(equipment_id)
        return bool(instance and instance.equipment_type == "reporting_station")

    def _apply_equipment_role(self, equipment_id: str, settings: dict[str, Any]) -> None:
        instance = self.equipment_instance(equipment_id)
        settings["equipment_type"] = instance.equipment_type if instance else ""
        terminal = self.is_reporting_station(equipment_id)
        settings["terminal_output"] = terminal
        if terminal:
            settings.update(throughput=0.0, human_throughput=0.0, required_staff=0,
                            output_data=False, output_specimens=False)

    def add_equipment_node(
        self,
        equipment_id: str,
        window: sg.Window | None = None,
        *,
        throughput: float = 0.0,
        human_throughput: float = 0.0,
        capacity: int = 1,
        required_staff: int = 0,
        description: str = "",
        enabled: bool = True,
        output_data: bool = False,
        output_specimens: bool = False,
    ) -> str:
        """Add a workflow node that references one physical equipment instance."""

        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        if self.equipment_instance(equipment_id) is None:
            raise ValueError(f"Equipment {equipment_id} is not available in the active project.")
        settings = self._equipment_node_settings(
            throughput, human_throughput, capacity, required_staff, description, enabled,
            output_data, output_specimens,
        )
        self._apply_equipment_role(equipment_id, settings)
        node = self.current_document.workflow.create_node(
            "Reporting Station" if settings["terminal_output"] else "Equipment",
            service_time=settings["throughput"],
            kind="equipment",
            metadata={"equipment_id": equipment_id, "node_type": "equipment", **settings},
        )
        node.widget.node_type = "equipment"
        self.selected_node_id = node.node_id
        self.persist_current_workflow(window)
        return node.node_id

    @staticmethod
    def _equipment_node_settings(
        throughput: float,
        human_throughput: float,
        capacity: int,
        required_staff: int,
        description: str,
        enabled: bool,
        output_data: bool = False,
        output_specimens: bool = False,    ) -> dict[str, Any]:
        if not math.isfinite(throughput) or not math.isfinite(human_throughput) or throughput < 0 or human_throughput < 0:
            raise ValueError("Processing rates must be finite, non-negative numbers.")
        if capacity < 1 or required_staff < 0:
            raise ValueError("Capacity must be at least 1 and required staff cannot be negative.")
        return {
            "throughput": float(throughput),
            "human_throughput": float(human_throughput),
            "capacity": int(capacity),
            "required_staff": int(required_staff),
            "description": str(description),
            "enabled": bool(enabled),
            "output_data": bool(output_data),
            "output_specimens": bool(output_specimens),
        }

    def bind_selected_equipment(
        self,
        equipment_id: str,
        window: sg.Window | None = None,
        *,
        throughput: float | None = None,
        human_throughput: float | None = None,
        capacity: int | None = None,
        required_staff: int | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        output_data: bool | None = None,
        output_specimens: bool | None = None,
    ) -> None:
        """Configure an equipment node while preserving its stable physical reference."""

        node = self.selected_node()
        if node is None or node.kind != "equipment":
            raise ValueError("Select an Equipment Node first.")
        if self.equipment_instance(equipment_id) is None and equipment_id != node.metadata.get("equipment_id"):
            raise ValueError(f"Equipment {equipment_id} is not available in the active project.")
        settings = self._equipment_node_settings(
            node.widget.throughput if throughput is None else throughput,
            node.widget.human_throughput if human_throughput is None else human_throughput,
            node.widget.capacity if capacity is None else capacity,
            node.widget.required_staff if required_staff is None else required_staff,
            node.widget.description if description is None else description,
            node.widget.enabled if enabled is None else enabled,
            node.metadata.get("output_data", False) if output_data is None else output_data,
            node.metadata.get("output_specimens", False) if output_specimens is None else output_specimens,
        )
        if self.is_reporting_station(equipment_id) and self.current_document.workflow.graph.outgoing_edges(node.node_id):
            raise ValueError("Remove outgoing connections before choosing a Reporting Station.")
        self._apply_equipment_role(equipment_id, settings)
        self.current_document.workflow.update_node_properties(
            node.node_id,
            service_time=settings["throughput"],
            metadata={"equipment_id": equipment_id, **settings},
        )
        self.persist_current_workflow(window)

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
            window["-WF-ACTIVE-SCENARIO-"].update(values=[], value="")
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
        scenario_labels = {f"{s.name} [{s.scenario_id[:8]}]": s for s in workflow.scenarios}
        active_label = next((label for label, s in scenario_labels.items()
                             if s is workflow.active_scenario), "")
        window["-WF-ACTIVE-SCENARIO-"].update(values=list(scenario_labels), value=active_label)
        window["-WF-WIDGETS-"].update(
            values=[
                [
                    node.node_id,
                    self.node_display_name(node),
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
        equipment_resolution = self.resolve_equipment_node(node) if node.kind == "equipment" else None
        details = [
            f"Selected: {self.node_display_name(node)}",
            f"ID: {node.node_id}",
            f"Type: {node.widget.node_type}",
            f"Status: {'Enabled' if node.widget.enabled else 'Disabled'}",
            f"Incoming edges: {incoming}",
            f"Outgoing edges: {outgoing}",
        ]
        if node.widget.node_type == "order adapter":
            details.append(f"Adapter type: {node.metadata.get('adapter_type', 'simulation')}")
            details.append(f"Description: {node.widget.description}")
        else:
            details.extend([
                f"Processing Capacity (per hours): {node.widget.throughput:g} ({_capacity_time_label(node.widget.throughput)})",
                f"Human Processing Capacity (per hour): {node.widget.human_throughput:g} ({_capacity_time_label(node.widget.human_throughput)})",
                f"Capacity: {node.widget.capacity}",
                f"Required staff: {node.widget.required_staff}",
            ])
        if node.widget.node_type == "specimens processing" and node.kind != "equipment":
            details.append(f"Staff batch size: {node.metadata.get('batch_size', DEFAULT_BATCH_SIZE)}")
            details.append(f"Maximum batch wait: {node.metadata.get('batch_wait_minutes', DEFAULT_BATCH_WAIT_MINUTES):g} minutes")
        if equipment_resolution is not None:
            details.append(f"Equipment: {equipment_resolution.equipment_id}")
            details.append(
                "Equipment reference: Resolved"
                if equipment_resolution.resolved
                else "Equipment reference: Missing / unresolved"
            )
        analyzer_tests = node.widget.metadata.get("analyzer_tests", [])
        if analyzer_tests:
            details.append(f"Analyzer tests: {', '.join(str(test) for test in analyzer_tests)}")
        if node.metadata:
            details.append(f"Metadata: {node.metadata}")
        window["-WF-DETAILS-"].update("\n".join(details))
        self.update_analyzer_tests_button(window)

    def is_analyzer_node(self, node: WorkflowNode | None) -> bool:
        if node is None:
            return False
        if node.widget.node_type in {"automated analyzer", "manual analyzer"}:
            return True
        instance = self.equipment_instance(str(node.metadata.get("equipment_id", "")))
        return bool(node.kind == "equipment" and instance
                    and instance.equipment_type == "chemistry_analyzer")

    def update_analyzer_tests_button(self, window: sg.Window) -> None:
        """Enable the test selection button only for analyzer nodes."""

        button = window["-WF-SELECT-TESTS-"]
        node = self.selected_node()
        is_analyzer = self.is_analyzer_node(node)
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
        window["-WF-GRAPH-SCROLL-"].contents_changed()
        window.refresh()

    def refresh(self, window: sg.Window) -> None:
        self.refresh_document_table(window)
        self.refresh_summary(window)
        self.rerender(window)
        self.update_graph_context_menu(window)

    def update_graph_context_menu(self, window: sg.Window, *, selected_node_id: str | None = None) -> None:
        """Disable the graph right-click menu.

        Connection editing now happens from the modal node forms, so the
        context menu is intentionally turned off to avoid stale selection
        behavior.
        """

        graph = window["-WF-GRAPH-"]
        try:
            window.TKRightClickMenu = None
        except Exception:
            pass
        try:
            graph.TKRightClickMenu = None
        except Exception:
            pass
        graph.set_right_click_menu(sg.MENU_RIGHT_CLICK_DISABLED)

    def build_graph_context_menu(self, *, selected_node_id: str | None = None) -> list[Any] | None:
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
        selected_node_id = selected_node_id if selected_node_id is not None else self.selected_node_id
        selected_node = self.current_document.workflow.get_node(selected_node_id) if selected_node_id is not None else None
        target_items = [
            f"Add connection to {node.widget.name}::{node.node_id}"
            for node in self._available_connection_targets(selected_node_id)
        ]
        outgoing_ids = {
            edge.target
            for edge in self.current_document.workflow.graph.outgoing_edges(selected_node_id or "")
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
        if self.is_analyzer_node(selected_node):
            menu.append("Select tests::select_analyzer_tests")
        return menu

    def _available_connection_targets(self, selected_node_id: str | None = None) -> list[WorkflowNode]:
        """Return the nodes that should appear in the Connect to menu."""

        if self.current_document is None:
            return []

        workflow = self.current_document.workflow
        selected_node_id = selected_node_id if selected_node_id is not None else self.selected_node_id
        if selected_node_id is None:
            return []

        source_node = workflow.get_node(selected_node_id)
        if source_node is None or source_node.metadata.get("terminal_output"):
            return []

        connected_ids = {edge.target for edge in workflow.graph.outgoing_edges(selected_node_id)}
        available_nodes: list[WorkflowNode] = []
        for node in workflow.graph.iter_nodes():
            if node.node_id == selected_node_id:
                continue
            if source_node.kind == "resource":
                if source_node.metadata.get("resource_type") != "patient" or node.widget.node_type != "order adapter":
                    continue
            elif node.widget.node_type == "order adapter":
                continue
            if node.kind == "resource":
                continue
            if node.node_id in connected_ids:
                continue
            if not self._is_valid_connection_target(node.node_id, selected_node_id=selected_node_id):
                continue
            available_nodes.append(node)
        return sorted(available_nodes, key=lambda node: node.widget.name.lower())

    def connection_target_choices(self) -> list[tuple[str, str]]:
        """Return legal connection targets with labels suitable for the editor UI."""

        choices: list[tuple[str, str]] = []
        for node in self._available_connection_targets():
            display_name = self.node_display_name(node)
            equipment_id = str(node.metadata.get("equipment_id", "")) if node.kind == "equipment" else ""
            label = f"{display_name} — {equipment_id}" if equipment_id else display_name
            choices.append((node.node_id, f"{label} [{node.node_id}]"))
        return choices

    def _is_valid_connection_target(self, target_node_id: str, *, selected_node_id: str | None = None) -> bool:
        """Return whether the current selection can connect to ``target_node_id``.

        The editor keeps the submenu focused on legal targets: the selected node
        itself is excluded, and we avoid targets that would introduce a cycle by
        connecting back into the currently selected node.
        """

        if self.current_document is None:
            return True
        selected_node_id = selected_node_id if selected_node_id is not None else self.selected_node_id
        if selected_node_id is None:
            return True
        if target_node_id == selected_node_id:
            return False

        workflow = self.current_document.workflow
        seen: set[str] = set()
        stack = [target_node_id]
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            if node_id == selected_node_id:
                return False
            for edge in workflow.graph.outgoing_edges(node_id):
                if edge.target not in seen:
                    stack.append(edge.target)
        return True

    def configure_node_staff(self, node: WorkflowNode) -> None:
        from app.system.scenario_editor import node_staff_dialog
        if node_staff_dialog(self.current_document.workflow, node):
            self.persist_current_workflow()

    def persist_current_workflow(self, window: sg.Window | None = None) -> str:
        """Write the current workflow document to disk."""

        if self.current_document is None:
            raise ValueError("No workflow is loaded.")
        if self.on_change is not None:
            self.on_change()
            if window is not None:
                window["-WF-STATUS-"].update("Status: Unsaved project changes", text_color="#b45309")
                window.refresh()
            return ""
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
        if window is not None:
            self.update_graph_context_menu(window)
        return workflow_name

    def add_node(self, window: sg.Window | None = None) -> str:
        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        selected_node_id = self.selected_node_id
        widget = self.current_document.workflow.graph.create_default_widget()
        node = self.current_document.workflow.graph.add_widget(widget)
        self.selected_node_id = selected_node_id
        self.persist_current_workflow(window)
        if window is not None:
            self.update_graph_context_menu(window)
        return node.node_id

    def add_order_adapter_node(self, window: sg.Window | None = None) -> str:
        """Create an explicit workflow source with simulation adapter defaults."""

        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        node = self.current_document.workflow.create_node(
            "Order Adapter",
            kind="order_adapter",
            metadata={"node_type": "order adapter", "adapter_type": "simulation", "enabled": True},
        )
        self.selected_node_id = node.node_id
        self.persist_current_workflow(window)
        return node.node_id

    def add_resource_node(self, resource_type: str, adapter_type: str, enabled: bool = True,
                          window: sg.Window | None = None) -> str:
        """Declare a configured resource outside the order-routing graph."""

        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        if adapter_type not in RESOURCE_ADAPTERS.get(resource_type, {}):
            raise ValueError("Unsupported Resource type or adapter.")
        if any(node.kind == "resource" and node.metadata.get("resource_type") == resource_type
               for node in self.current_document.workflow.graph.iter_nodes()):
            raise ValueError(f"This workflow already has a {resource_type.title()} Resource node.")
        node = self.current_document.workflow.create_node(
            f"{resource_type.title()} Resource", kind="resource",
            metadata={"node_type": "resource", "resource_type": resource_type,
                      "adapter_type": adapter_type, "enabled": enabled},
        )
        self.selected_node_id = node.node_id
        self.persist_current_workflow(window)
        return node.node_id

    def update_resource(self, node_id: str, resource_type: str, adapter_type: str, enabled: bool,
                        window: sg.Window | None = None) -> None:
        if adapter_type not in RESOURCE_ADAPTERS.get(resource_type, {}):
            raise ValueError("Unsupported Resource type or adapter.")
        node = self.current_document.workflow.get_node(node_id) if self.current_document else None
        if node is None or node.kind != "resource" or node.metadata.get("resource_type") != resource_type:
            raise ValueError("Select a matching Resource node first.")
        node.metadata["adapter_type"] = adapter_type
        node.metadata["enabled"] = enabled
        node.widget.metadata.update(node.metadata)
        node.widget.enabled = enabled
        self.persist_current_workflow(window)

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
        from app.system.scenario_editor import configure_scenario
        workflow = self.current_document.workflow
        while any(s.name == scenario_name for s in workflow.scenarios):
            scenario_name += ' copy'
        settings = scenario_settings(workflow, Scenario(name=''))
        configure_scenario(workflow, name=scenario_name, staff=settings['staff_capacity'],
                           arrival_minutes=0)
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

    def node_id_for_table_row(self, row_index: int) -> str | None:
        """Map the visible workflow-node table row to its stable graph node ID."""

        if self.current_document is None:
            return None
        nodes = self.current_document.workflow.graph.iter_nodes()
        if 0 <= row_index < len(nodes):
            return nodes[row_index].node_id
        return None

    def focus_node(self, window: sg.Window, node_id: str) -> None:
        """Select a node and center it in the scrollable graph viewport."""

        if self.current_document is None or self.current_document.workflow.get_node(node_id) is None:
            return
        self.selected_node_id = node_id
        self.refresh_summary(window)
        self.rerender(window)
        bounds = self.bounds.get(node_id)
        if bounds is not None:
            self._center_graph_point(window, (bounds.left + bounds.right) / 2, (bounds.top + bounds.bottom) / 2)

    def focus_diagram(self, window: sg.Window) -> None:
        """Center the rendered diagram when the workflow editor opens."""

        if not self.bounds:
            return
        left = min(bounds.left for bounds in self.bounds.values())
        right = max(bounds.right for bounds in self.bounds.values())
        top = max(bounds.top for bounds in self.bounds.values())
        bottom = min(bounds.bottom for bounds in self.bounds.values())
        self._center_graph_point(window, (left + right) / 2, (top + bottom) / 2)

    def _center_graph_point(self, window: sg.Window, x: float, y: float) -> None:
        """Translate logical Graph coordinates into the containing Tk scroll canvas."""

        graph_element = window["-WF-GRAPH-"]
        graph_canvas = graph_element.Widget
        scroll_canvas = _scroll_canvas_for_column(window["-WF-GRAPH-SCROLL-"])
        if graph_canvas is None or scroll_canvas is None:
            return
        window.refresh()
        pixel_x, pixel_y = graph_element._convert_xy_to_canvas_xy(x, y)
        target_x = scroll_canvas.canvasx(0) + graph_canvas.winfo_rootx() - scroll_canvas.winfo_rootx() + pixel_x
        target_y = scroll_canvas.canvasy(0) + graph_canvas.winfo_rooty() - scroll_canvas.winfo_rooty() + pixel_y
        region = scroll_canvas.cget("scrollregion").split()
        if len(region) != 4:
            return
        left, top, right, bottom = (float(value) for value in region)
        scroll_canvas.xview_moveto(_center_scroll_fraction(target_x, scroll_canvas.winfo_width(), left, right))
        scroll_canvas.yview_moveto(_center_scroll_fraction(target_y, scroll_canvas.winfo_height(), top, bottom))

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
        enabled: bool = True,
        batch_size: int | None = None,
        batch_wait_minutes: float | None = None,
        attend_equipment_id: str | None = None,
        incoming_connection_node_id: str | None = None,
        outgoing_connection_node_id: str | None = None,
        window: sg.Window | None = None,
    ) -> None:
        """Apply form values to a node and persist the workflow."""

        node = self.current_document.workflow.get_node(node_id) if self.current_document else None
        if node is None:
            raise ValueError("Selected node is no longer available.")
        if attend_equipment_id and self.equipment_instance(attend_equipment_id) is None and attend_equipment_id != node.metadata.get("attend_equipment_id"):
            raise ValueError("The staff attendance equipment is not available in this project.")
        if node_type == "order adapter":
            raise ValueError("Use Order Adapter settings for this node type.")

        batch_size, batch_wait_minutes = validate_batch_settings(
            node.metadata.get("batch_size", DEFAULT_BATCH_SIZE) if batch_size is None else batch_size,
            node.metadata.get("batch_wait_minutes", DEFAULT_BATCH_WAIT_MINUTES) if batch_wait_minutes is None else batch_wait_minutes)

        if node.kind == "order_adapter":
            node.metadata.pop("adapter_type", None)
            node.widget.metadata.pop("adapter_type", None)

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
                "batch_size": batch_size,
                "batch_wait_minutes": batch_wait_minutes,
                "enabled": enabled,
                "attend_equipment_id": node.metadata.get("attend_equipment_id", "") if attend_equipment_id is None else attend_equipment_id,
            },
            kind="task" if node.kind == "order_adapter" else node.kind,
        )
        node.widget.node_type = node_type
        node.widget.throughput = throughput
        node.widget.human_throughput = human_throughput
        node.widget.capacity = capacity
        node.widget.enabled = enabled
        node.widget.description = description
        node.widget.required_staff = required_staff
        self._apply_connection_choices(
            node_id,
            incoming_connection_node_id=incoming_connection_node_id,
            outgoing_connection_node_id=outgoing_connection_node_id,
        )
        self.persist_current_workflow(window)
        if window is not None:
            self.update_graph_context_menu(window)

    def update_order_adapter_from_form(
        self,
        node_id: str,
        *,
        node_type: str,
        name: str,
        adapter_type: str,
        description: str,
        enabled: bool = True,
        window: sg.Window | None = None,
    ) -> None:
        """Configure an order-arrival source without specimen-processing fields."""

        node = self.current_document.workflow.get_node(node_id) if self.current_document else None
        if node is None:
            raise ValueError("Selected node is no longer available.")
        if node_type != "order adapter" or adapter_type not in ADAPTER_TYPES:
            raise ValueError("Unsupported order adapter type.")
        if any(self.current_document.workflow.get_node(edge.source).kind != "resource"
               for edge in self.current_document.workflow.graph.incoming_edges(node_id)):
            raise ValueError("Order Adapter nodes cannot have incoming order-flow connections.")
        self.current_document.workflow.update_node_properties(
            node_id,
            name=name,
            service_time=0.0,
            kind="order_adapter",
            metadata={
                "node_type": "order adapter",
                "adapter_type": adapter_type,
                "description": description,
                "enabled": enabled,
                "throughput": 0.0,
                "human_throughput": 0.0,
                "required_staff": 0,
                "capacity": 1,
            },
        )
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
        resource_link = (
            source_node.kind == "resource"
            and source_node.metadata.get("resource_type") == "patient"
            and target_node.widget.node_type == "order adapter"
        )
        if target_node.widget.node_type == "order adapter" and not resource_link:
            raise ValueError("Order Adapter nodes cannot have incoming connections.")
        if target_node.kind == "resource" or (source_node.kind == "resource" and not resource_link):
            raise ValueError("Patient Resources may only connect to Order Adapters.")
        if not self._is_valid_connection_target(target_node_id):
            raise ValueError("This connection would create a cycle.")

        self.current_document.workflow.graph.connect_nodes(source_node.node_id, target_node.node_id)
        self.persist_current_workflow(window)
        if window is not None:
            self.update_graph_context_menu(window)

    def connect_selected_node_to_many(self, target_node_ids: list[str], window: sg.Window | None = None) -> None:
        """Connect one source to multiple legal destinations and persist once."""

        if not target_node_ids:
            return
        if self.current_document is None or self.selected_node_id is None:
            raise ValueError("Select a source node in a workflow first.")
        available_ids = {node_id for node_id, _ in self.connection_target_choices()}
        if any(node_id not in available_ids for node_id in target_node_ids):
            raise ValueError("One or more connection targets are no longer available.")
        for target_node_id in dict.fromkeys(target_node_ids):
            self.current_document.workflow.graph.connect_nodes(self.selected_node_id, target_node_id)
        self.persist_current_workflow(window)

    def edge_at(self, x: float, y: float, tolerance: float = 8) -> tuple[str, str] | None:
        """Find the nearest rendered connection within a small click tolerance."""
        if self.current_document is None:
            return None
        workflow = self.current_document.workflow
        layout = self.renderer.layout_engine
        positions = layout.layout(workflow)
        nearest = None
        distance = tolerance
        for edge in workflow.graph.edges:
            source, target = workflow.get_node(edge.source), workflow.get_node(edge.target)
            if source is None or target is None:
                continue
            sx, sy = positions[edge.source]
            tx, ty = positions[edge.target]
            sy -= layout.node_size(source)[1] / 2
            ty += layout.node_size(target)[1] / 2
            middle = (sy + ty) / 2
            points = [(sx, sy), (sx, middle), (tx, middle), (tx, ty)]
            for (ax, ay), (bx, by) in zip(points, points[1:]):
                length_squared = (bx - ax) ** 2 + (by - ay) ** 2
                fraction = max(0, min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay))
                                      / length_squared)) if length_squared else 0
                candidate = math.hypot(x - ax - fraction * (bx - ax), y - ay - fraction * (by - ay))
                if candidate <= distance:
                    distance, nearest = candidate, (edge.source, edge.target)
        return nearest

    def remove_edge(self, source_id: str, target_id: str, window: sg.Window | None = None) -> None:
        if self.current_document is None:
            return
        graph = self.current_document.workflow.graph
        if not any(edge.source == source_id and edge.target == target_id for edge in graph.edges):
            return
        graph.disconnect_nodes(source_id, target_id)
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
        if window is not None:
            self.update_graph_context_menu(window)

    def _next_node_name(self, suffix: str) -> str:
        if self.current_document is None:
            return f"Node {self.workflow_counter + 1}"
        return f"Node {len(self.current_document.workflow.graph.nodes) + 1}"

    def add_connected_node(self, window: sg.Window | None = None) -> str:
        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        if self.selected_node_id is None:
            raise ValueError("Select a node first.")
        if self.current_document.workflow.get_node(self.selected_node_id).kind == "resource":
            raise ValueError("Use Connect Nodes to link a Patient Resource to an Order Adapter.")
        node = self.current_document.workflow.create_connected_node(
            self.selected_node_id,
            self._next_node_name("Step"),
            0.0,
        )
        self.selected_node_id = node.node_id
        self.persist_current_workflow(window)
        if window is not None:
            self.update_graph_context_menu(window)
        return node.node_id

    def add_branch_node(self, window: sg.Window | None = None) -> str:
        if self.current_document is None:
            raise ValueError("Create or load a workflow first.")
        if self.selected_node_id is None:
            raise ValueError("Select a node first.")
        if self.current_document.workflow.get_node(self.selected_node_id).kind == "resource":
            raise ValueError("Resource nodes cannot create processing branches.")
        node = self.current_document.workflow.create_branch_node(
            self.selected_node_id,
            self._next_node_name("Branch"),
        )
        self.selected_node_id = node.node_id
        self.persist_current_workflow(window)
        if window is not None:
            self.update_graph_context_menu(window)
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
        enabled: bool = True,
        incoming_connection_node_id: str | None = None,
        outgoing_connection_node_id: str | None = None,
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
            metadata={**metadata, "capacity": capacity, "enabled": enabled},
            kind=kind,
        )
        node.widget.capacity = capacity
        node.widget.enabled = enabled
        self._apply_connection_choices(
            node.node_id,
            incoming_connection_node_id=incoming_connection_node_id,
            outgoing_connection_node_id=outgoing_connection_node_id,
        )
        self.persist_current_workflow(window)
        if window is not None:
            self.update_graph_context_menu(window)

    def _apply_connection_choices(
        self,
        node_id: str,
        *,
        incoming_connection_node_id: str | None,
        outgoing_connection_node_id: str | None,
    ) -> None:
        """Add connections chosen in a single-choice form without removing other branches."""

        if self.current_document is None:
            return
        workflow = self.current_document.workflow
        current_incoming = {edge.source for edge in workflow.graph.incoming_edges(node_id)}
        current_outgoing = {edge.target for edge in workflow.graph.outgoing_edges(node_id)}
        desired_incoming = {incoming_connection_node_id} if incoming_connection_node_id and incoming_connection_node_id != node_id else set()
        desired_outgoing = {outgoing_connection_node_id} if outgoing_connection_node_id and outgoing_connection_node_id != node_id else set()

        for source_id in sorted(desired_incoming - current_incoming):
            workflow.graph.connect_nodes(source_id, node_id)
        for target_id in sorted(desired_outgoing - current_outgoing):
            workflow.graph.connect_nodes(node_id, target_id)

    def delete_selected_node(self, window: sg.Window | None = None) -> None:
        node = self.selected_node()
        if node is None:
            raise ValueError("Select a node first.")
        self.current_document.workflow.delete_node(node.node_id)
        self.selected_node_id = None
        self.persist_current_workflow(window)
        if window is not None:
            self.update_graph_context_menu(window)

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

        metadata = {"analyzer_tests": list(test_codes)}
        instance = self.equipment_instance(str(node.metadata.get("equipment_id", "")))
        if instance is not None:
            metadata["equipment_type"] = instance.equipment_type
        self.current_document.workflow.update_node_properties(node_id, metadata=metadata)
        node.widget.metadata["analyzer_tests"] = list(test_codes)
        self.persist_current_workflow(window)
        if window is not None:
            self.update_graph_context_menu(window)


def _capacity_time_label(value: object) -> str:
    """Display nominal minutes for one test/specimen at a per-hour rate."""

    try:
        rate = float(value)
    except (TypeError, ValueError):
        return "— min/item"
    if not math.isfinite(rate) or rate < 0:
        return "— min/item"
    if rate == 0:
        return "— min/item (capacity is 0)"
    return f"{60 / rate:.4g} min/item"


_CAPACITY_TIME_TOOLTIP = "60 ÷ capacity per hour. One item is a test for analyzers or a specimen for other equipment."


def _show_node_properties_dialog(
    node: WorkflowNode | None,
) -> dict[str, Any] | None:
    """Prompt for editable node properties."""

    if node is None:
        return None

    layout = [
        [sg.Text("Name"), sg.Input(node.widget.name, key="-NAME-", expand_x=True)],
        [
            sg.Text("Processing Capacity (per hours)"),
            sg.Input(str(node.widget.service_time), key="-SERVICE-", size=(12, 1), enable_events=True),
            sg.Text(_capacity_time_label(node.widget.service_time), key="-SERVICE-TIME-", size=(29, 1), tooltip=_CAPACITY_TIME_TOOLTIP),
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
        [sg.Checkbox("Enabled", default=node.widget.enabled, key="-ENABLED-")],
        [sg.Button("Save", key="-SAVE-", bind_return_key=True), sg.Button("Cancel")],
    ]
    window = create_window("Node Properties", layout=layout, modal=True, finalize=True, keep_on_top=True)
    try:
        while True:
            event, values = window.read()
            if event in ("Cancel", sg.WIN_CLOSED):
                return None
            if event == "-SERVICE-":
                window["-SERVICE-TIME-"].update(_capacity_time_label(values.get("-SERVICE-")))
                continue
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
                    "enabled": bool(values.get("-ENABLED-", False)),
                }
    finally:
        window.close()


def _show_equipment_node_dialog(
    controller: WorkflowEditorController,
    node: WorkflowNode | None = None,
) -> dict[str, Any] | None:
    """Edit the physical reference and workflow-local operating settings."""

    choice_items = controller.equipment_choices()
    if not choice_items and node is None:
        _notify("No equipment exists in Floor View. Place equipment before creating an Equipment Node.")
        return None
    current_id = str(node.metadata.get("equipment_id", "")) if node is not None else ""
    if current_id and all(equipment_id != current_id for equipment_id, _ in choice_items):
        choice_items.insert(0, (current_id, f"{current_id} — Missing Equipment"))
    label_to_id = {label: equipment_id for equipment_id, label in choice_items}
    default_label = next(
        (label for equipment_id, label in choice_items if equipment_id == current_id),
        choice_items[0][1],
    )
    layout = [
        [sg.Text("Equipment Resource", font=("Arial", 16, "bold"))],
        [sg.Text("Shared node settings — all scenarios", text_color="#64748b")],
        [
            sg.Text("Resource", size=(12, 1)),
            sg.Combo(
                list(label_to_id),
                default_value=default_label,
                key="-WF-EQUIPMENT-",
                enable_events=True,
                readonly=True,
                size=(38, 1),
            ),
        ],
        [sg.Text("Processing Capacity (per hours)"), sg.Input(str(node.widget.throughput if node else 0.0), key="-WF-EQUIPMENT-THROUGHPUT-", size=(14, 1), enable_events=True), sg.Text(_capacity_time_label(node.widget.throughput if node else 0.0), key="-WF-EQUIPMENT-THROUGHPUT-TIME-", size=(29, 1), tooltip=_CAPACITY_TIME_TOOLTIP)],
        [sg.Text("Human Processing Capacity (per hour)"), sg.Input(str(node.widget.human_throughput if node else 0.0), key="-WF-EQUIPMENT-HUMAN-", size=(14, 1), enable_events=True), sg.Text(_capacity_time_label(node.widget.human_throughput if node else 0.0), key="-WF-EQUIPMENT-HUMAN-TIME-", size=(29, 1), tooltip=_CAPACITY_TIME_TOOLTIP)],
        [sg.Text("Capacity"), sg.Input(str(node.widget.capacity if node else 1), key="-WF-EQUIPMENT-CAPACITY-", size=(14, 1))],
        [sg.Text("Required staff"), sg.Input(str(node.widget.required_staff if node else 0), key="-WF-EQUIPMENT-STAFF-", size=(14, 1))],
        [sg.Button("Allowed staff", key="-WF-EQUIPMENT-ALLOWED-STAFF-", disabled=node is None,
                   tooltip="Save a new node before assigning staff."),
         sg.Text(f"Scenario: {controller.current_document.workflow.active_scenario.name}", text_color="#2563eb")],
        [sg.Text("Description"), sg.Multiline(node.widget.description if node else "", key="-WF-EQUIPMENT-DESC-", size=(42, 4))],
        [sg.Frame("Output", [[
            sg.Checkbox("Data", default=bool(node.metadata.get("output_data", False)) if node else False,
                        key="-WF-EQUIPMENT-OUTPUT-DATA-"),
            sg.Checkbox("Specimens", default=bool(node.metadata.get("output_specimens", False)) if node else False,
                        key="-WF-EQUIPMENT-OUTPUT-SPECIMENS-"),
        ]])],
        [sg.Checkbox("Enabled", default=node.widget.enabled if node else True, key="-WF-EQUIPMENT-ENABLED-")],
        [sg.Text("Reporting Station: visual endpoint; no processing time or outgoing flow.",
                 key="-WF-EQUIPMENT-ENDPOINT-", visible=False, text_color="#2563eb")],
        [sg.Text("Operating settings belong to this workflow node, not the floor equipment.", text_color="#64748b")],
        [sg.Push(), sg.Button("Save", key="-SAVE-", bind_return_key=True), sg.Button("Cancel")],
    ]
    window = create_window("Equipment Node", layout, modal=True, finalize=True, keep_on_top=True)
    def update_role_fields() -> None:
        equipment_id = label_to_id.get(window["-WF-EQUIPMENT-"].get(), "")
        terminal = controller.is_reporting_station(equipment_id)
        window["-WF-EQUIPMENT-ENDPOINT-"].update(visible=terminal)
        window["-WF-EQUIPMENT-ALLOWED-STAFF-"].update(disabled=terminal or node is None)
        for key in ("THROUGHPUT", "HUMAN", "STAFF", "OUTPUT-DATA", "OUTPUT-SPECIMENS"):
            window[f"-WF-EQUIPMENT-{key}-"].update(disabled=terminal)
            if terminal:
                window[f"-WF-EQUIPMENT-{key}-"].update(False if key.startswith("OUTPUT") else "0")

    def update_capacity_times() -> None:
        for field in ("THROUGHPUT", "HUMAN"):
            window[f"-WF-EQUIPMENT-{field}-TIME-"].update(
                _capacity_time_label(window[f"-WF-EQUIPMENT-{field}-"].get()))

    update_role_fields()
    update_capacity_times()
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                return None
            if event == "-WF-EQUIPMENT-ALLOWED-STAFF-" and node is not None:
                controller.configure_node_staff(node)
                continue
            if event == "-WF-EQUIPMENT-":
                update_role_fields()
                update_capacity_times()
                continue
            if event in ("-WF-EQUIPMENT-THROUGHPUT-", "-WF-EQUIPMENT-HUMAN-"):
                update_capacity_times()
                continue
            if event == "-SAVE-":
                equipment_id = label_to_id.get(str(values.get("-WF-EQUIPMENT-", "")))
                if equipment_id is None:
                    _notify("Select an equipment resource.")
                    continue
                try:
                    throughput = float(values["-WF-EQUIPMENT-THROUGHPUT-"])
                    human_throughput = float(values["-WF-EQUIPMENT-HUMAN-"])
                    capacity = int(values["-WF-EQUIPMENT-CAPACITY-"])
                    required_staff = int(values["-WF-EQUIPMENT-STAFF-"])
                    controller._equipment_node_settings(
                        throughput, human_throughput, capacity, required_staff,
                        str(values["-WF-EQUIPMENT-DESC-"]), bool(values["-WF-EQUIPMENT-ENABLED-"]),
                    )
                except (TypeError, ValueError) as exc:
                    _notify(f"Invalid equipment settings: {exc}")
                    continue
                return {
                    "equipment_id": equipment_id,
                    "throughput": throughput,
                    "human_throughput": human_throughput,
                    "capacity": capacity,
                    "required_staff": required_staff,
                    "description": str(values["-WF-EQUIPMENT-DESC-"]).strip(),
                    "enabled": bool(values["-WF-EQUIPMENT-ENABLED-"]),
                    "output_data": bool(values.get("-WF-EQUIPMENT-OUTPUT-DATA-", False)),
                    "output_specimens": bool(values.get("-WF-EQUIPMENT-OUTPUT-SPECIMENS-", False)),
                }
    finally:
        window.close()


def _show_connection_target_dialog(controller: WorkflowEditorController) -> list[str] | None:
    """Select legal downstream nodes for the currently selected node."""

    choice_items = controller.connection_target_choices()
    if not choice_items:
        _notify("No compatible unconnected nodes are available.")
        return None
    checkbox_rows = [
        [sg.Checkbox(label, key=f"-WF-CONNECTION-TARGET-{index}-")]
        for index, (_, label) in enumerate(choice_items)
    ]
    layout = [
        [sg.Text("Connect Nodes", font=("Arial", 16, "bold"))],
        [sg.Text("Check each destination to connect:")],
        [sg.Column(checkbox_rows, size=(440, min(360, max(60, len(choice_items) * 32))), scrollable=True, vertical_scroll_only=True)],
        [sg.Push(), sg.Button("Connect", key="-CONNECT-", bind_return_key=True), sg.Button("Cancel")],
    ]
    window = create_window("Connect Workflow Nodes", layout, modal=True, finalize=True, keep_on_top=True)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                return None
            if event == "-CONNECT-":
                selected = [
                    node_id
                    for index, (node_id, _) in enumerate(choice_items)
                    if values.get(f"-WF-CONNECTION-TARGET-{index}-", False)
                ]
                if selected:
                    return selected
                _notify("Select at least one destination node.")
    finally:
        window.close()


def _show_run_workflow_dialog(workflow: Workflow, *, selected_scenario_id=None) -> tuple[str, int, int | None] | None:
    """Choose a persisted scenario and size for one project simulation run."""

    choices = [(scenario.scenario_id, scenario.name) for scenario in workflow.scenarios]
    if not choices:
        _notify("This workflow has no scenario to run.")
        return None
    labels = [f"{name} [{scenario_id[:8]}]" for scenario_id, name in choices]
    selected_scenario_id = selected_scenario_id or workflow.active_scenario.scenario_id
    layout = [
        [sg.Text("Run Workflow", font=("Arial", 16, "bold"))],
        [sg.Text("Active scenario"), sg.Combo(labels, default_value=next((label for label, (sid, _) in zip(labels, choices) if sid == selected_scenario_id), labels[0]), key="-RUN-SCENARIO-", readonly=True, size=(36, 1))],
        [sg.Text("Orders to generate"), sg.Input("10", key="-RUN-ORDERS-", size=(12, 1))],
        [sg.Text("Random seed (optional)"), sg.Input("", key="-RUN-SEED-", size=(12, 1))],
        [sg.Push(), sg.Button("Run", key="-RUN-", bind_return_key=True), sg.Button("Cancel")],
    ]
    window = create_window("Run Workflow", layout, modal=True, finalize=True, keep_on_top=True)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                return None
            if event == "-RUN-":
                try:
                    orders_number = int(values["-RUN-ORDERS-"])
                    seed_text = str(values["-RUN-SEED-"]).strip()
                    random_seed = int(seed_text) if seed_text else None
                    if orders_number <= 0:
                        raise ValueError("Number of orders must be positive.")
                except (TypeError, ValueError) as exc:
                    _notify(f"Invalid run settings: {exc}")
                    continue
                selected_label = str(values.get("-RUN-SCENARIO-", ""))
                if selected_label not in labels:
                    _notify("Select a scenario.")
                    continue
                scenario_id = choices[labels.index(selected_label)][0]
                workflow.select_scenario(scenario_id)
                return scenario_id, orders_number, random_seed
    finally:
        window.close()


@login_required
def open_workflow_editor_window(
    current_document: WorkflowDocument | None = None,
    repository: WorkflowRepository | None = None,
    on_change: Callable[[], None] | None = None,
    equipment_instances: list[ProjectEquipmentInstance] | None = None,
    run_simulation: Callable[[str, int, int | None], Any] | None = None,
) -> None:
    """Open the interactive workflow editor window."""

    controller = WorkflowEditorController(
        repository=repository,
        current_document=current_document,
        on_change=on_change,
        equipment_instances=equipment_instances,
    )
    project_mode = on_change is not None

    layout = [
        [sg.Text("Workflow Editor", font=("Arial", 20, "bold"))],
        [
            sg.Text("Current workflow:"),
            sg.Text("No workflow loaded", key="-WF-NAME-"),
            sg.Text("Status: No workflow", key="-WF-STATUS-", text_color="#64748b"),
            sg.Text("Select a node and use the toolbar to edit the graph.", text_color="#475569"),
        ],
        [
            sg.Text(
                "Storage: Active Lab Tycoon project" if project_mode else f"Storage: {controller.repository.base_dir}",
                key="-WF-STORAGE-",
                size=(90, 1),
            )
        ],
        [sg.Text("Create a workflow to begin.", key="-WF-SUMMARY-")],
        [
            *(
                [
                    sg.Button("New Workflow", key="-WF-NEW-", button_color=("white", "green")),
                    sg.Button("Load Selected", key="-WF-LOAD-"),
                    sg.Button("Save Current", key="-WF-SAVE-"),
                ]
                if not project_mode else []
            ),
            sg.Button("Add Node", key="-WF-ADD-NODE-"),
            sg.Button("Add Order Adapter", key="-WF-ADD-ORDER-ADAPTER-"),
            sg.Button("Add Resource", key="-WF-ADD-RESOURCE-"),
            sg.Button("Add Branch Node", key="-WF-ADD-BRANCH-"),
            *([sg.Button("Add Equipment Node", key="-WF-ADD-EQUIPMENT-")] if project_mode else []),
            sg.Button("Connect Nodes", key="-WF-CONNECT-"),
            sg.Button("Edit Properties", key="-WF-PROPS-"),
            sg.Button("Select tests", key="-WF-SELECT-TESTS-", disabled=True),
            sg.Button("Delete Node", key="-WF-DELETE-", button_color=("white", "#b91c1c")),
            sg.Button("Add Scenario", key="-WF-ADD-SCENARIO-"),
            *([sg.Button("Run Workflow", key="-WF-RUN-")] if run_simulation is not None else []),
            sg.CloseButton("Close", button_color=DANGER_BUTTON_COLOR),
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
                    [sg.Text("Active scenario"), sg.Combo([], key="-WF-ACTIVE-SCENARIO-",
                        readonly=True, enable_events=True, size=(32, 1)),
                     sg.Button("Manage", key="-WF-MANAGE-SCENARIOS-")],
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
                            enable_events=True,
                        )
                    ],
                ],
                size=(440, 780),
                expand_x=True,
                expand_y=True,
            ),
        ],
    ]

    window = create_window(
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
    controller.focus_diagram(window)
    bring_window_to_front(window)

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
            if event == "-WF-WIDGETS-":
                selected_rows = values.get("-WF-WIDGETS-", [])
                if selected_rows:
                    node_id = controller.node_id_for_table_row(selected_rows[0])
                    if node_id is not None:
                        controller.focus_node(window, node_id)
                continue
            if event == "-WF-GRAPH-DOUBLE-":
                controller.refresh_summary(window)
                controller.rerender(window)
                node = controller.selected_node()
                if node is not None and node.kind == "resource":
                    settings = _show_resource_dialog(node)
                    if settings is not None:
                        controller.update_resource(node.node_id, *settings, window=window)
                elif node is not None and node.kind == "equipment":
                    settings = _show_equipment_node_dialog(controller, node)
                    if settings is not None:
                        controller.bind_selected_equipment(window=window, **settings)
                else:
                    result = _show_node_form_dialog(node, controller.equipment_choices(), controller.configure_node_staff,
                        controller.current_document.workflow.active_scenario.name)
                    if result is not None and node is not None:
                        if result["node_type"] == "order adapter":
                            controller.update_order_adapter_from_form(node.node_id, window=window, **result)
                        else:
                            controller.update_node_from_form(node.node_id, window=window, **result)
                controller.refresh(window)
                continue

            try:
                if event == "-WF-REMOVE-EDGE-":
                    controller.remove_edge(*values[event], window=window)
                    controller.refresh(window)
                    continue
                if isinstance(event, str) and "::" in event:
                    _, target_node_id = event.rsplit("::", 1)
                    if event.endswith("::select_analyzer_tests"):
                        node = controller.selected_node()
                        if node is None:
                            _notify("Select a node first.")
                            continue
                        if not controller.is_analyzer_node(node):
                            _notify("Select an Equipment node linked to an analyzer first.")
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
                    if not controller.is_analyzer_node(node):
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
                    controller.focus_diagram(window)
                elif event == "-WF-ACTIVE-SCENARIO-":
                    if controller.current_document is not None:
                        workflow = controller.current_document.workflow
                        label = values.get("-WF-ACTIVE-SCENARIO-")
                        scenario = next((s for s in workflow.scenarios
                            if f"{s.name} [{s.scenario_id[:8]}]" == label), None)
                        if scenario is not None:
                            workflow.select_scenario(scenario.scenario_id)
                            controller.persist_current_workflow(window)
                            controller.refresh_summary(window)
                elif event in ("-WF-ADD-SCENARIO-", "-WF-MANAGE-SCENARIOS-"):
                    if controller.current_document is None:
                        _notify("Create or load a workflow first.")
                        continue
                    from app.system.scenario_editor import scenario_dialog, open_scenario_manager
                    workflow = controller.current_document.workflow
                    if event == "-WF-ADD-SCENARIO-":
                        if scenario_dialog(workflow) is not None:
                            controller.persist_current_workflow(window)
                    else:
                        open_scenario_manager(workflow,
                            on_change=lambda: controller.persist_current_workflow(window),
                            run_simulation=run_simulation)
                    controller.refresh_summary(window)
                elif event == "-WF-RUN-" and run_simulation is not None:
                    if controller.current_document is None:
                        _notify("Create or load a workflow before running it.")
                        continue
                    run_settings = _show_run_workflow_dialog(controller.current_document.workflow)
                    if run_settings is not None:
                        controller.persist_current_workflow(window)
                        controller.refresh_summary(window)
                        summary = run_simulation(*run_settings)
                        sg.popup(
                            f"Scenario: {summary.scenario_name}\n"
                            f"Run ID: {summary.run_id}\n"
                            f"Orders generated: {summary.generated_orders}\n"
                            f"Specimens completed: {summary.completed_specimens}",
                            title="Workflow Run Complete",
                            keep_on_top=True,
                        )
                elif event == "-WF-ADD-NODE-":
                    controller.add_node(window)
                    controller.refresh(window)
                elif event == "-WF-ADD-ORDER-ADAPTER-":
                    controller.add_order_adapter_node(window)
                    controller.refresh(window)
                elif event == "-WF-ADD-RESOURCE-":
                    settings = _show_resource_dialog()
                    if settings is not None:
                        controller.add_resource_node(*settings, window=window)
                        controller.refresh(window)
                elif event == "-WF-ADD-BRANCH-":
                    controller.add_branch_node(window)
                    controller.refresh(window)
                elif event == "-WF-ADD-EQUIPMENT-":
                    settings = _show_equipment_node_dialog(controller)
                    if settings is not None:
                        controller.add_equipment_node(window=window, **settings)
                        controller.refresh(window)
                elif event == "-WF-CONNECT-":
                    if controller.selected_node() is None:
                        _notify("Select the source node first.")
                        continue
                    target_node_ids = _show_connection_target_dialog(controller)
                    if target_node_ids is not None:
                        controller.connect_selected_node_to_many(target_node_ids, window)
                        controller.refresh(window)
                elif event == "-WF-PROPS-":
                    node = controller.selected_node()
                    if node is not None and node.kind == "resource":
                        settings = _show_resource_dialog(node)
                        if settings is not None:
                            controller.update_resource(node.node_id, *settings, window=window)
                            controller.refresh(window)
                        continue
                    if node is not None and node.kind == "equipment":
                        settings = _show_equipment_node_dialog(controller, node)
                        if settings is not None:
                            controller.bind_selected_equipment(window=window, **settings)
                            controller.refresh(window)
                        continue
                    result = _show_node_form_dialog(node, controller.equipment_choices(), controller.configure_node_staff,
                        controller.current_document.workflow.active_scenario.name)
                    if result is not None and node is not None:
                        if result["node_type"] == "order adapter":
                            controller.update_order_adapter_from_form(node.node_id, window=window, **result)
                        else:
                            controller.update_node_from_form(node.node_id, window=window, **result)
                        controller.refresh(window)
                elif event == "-WF-DELETE-":
                    controller.delete_selected_node(window)
                    controller.refresh(window)
            except (ValueError, KeyError, IndexError) as exc:
                _notify(str(exc))
    finally:
        window.close()


@login_required
def create_workflow_window(
    current_document: WorkflowDocument | None = None,
    repository: WorkflowRepository | None = None,
    on_change: Callable[[], None] | None = None,
    equipment_instances: list[ProjectEquipmentInstance] | None = None,
    run_simulation: Callable[[str, int, int | None], Any] | None = None,
) -> None:
    """Backward-compatible alias for opening the editor window."""

    open_workflow_editor_window(
        current_document=current_document,
        repository=repository,
        on_change=on_change,
        equipment_instances=equipment_instances,
        run_simulation=run_simulation,
    )
