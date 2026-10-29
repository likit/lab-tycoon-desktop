"""SimPy-based workflow simulation for LabTycoon."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from math import isfinite
import random
import sys
from typing import Callable

import simpy

from app.system.governance import (
    ACTION_REJECT_DUPLICATE,
    Decision,
    GovernanceConsultation,
    GovernanceEvent,
    GovernanceProvider,
    EVENT_DUPLICATE_ORDER_DETECTED,
    SopAsCodeGovernanceProvider,
)
from app.system.model import Scenario, Specimen, Workflow, WorkflowNode
from app.system.order_adapter import create_order_adapter
from app.system.order_sources import InMemoryOrderItem, InMemoryTest
from app.system.resource_adapter import patient_adapter_for_workflow
from app.system.storage import WorkflowDocument, WorkflowRepository
from app.system.staff_batching import staff_batch_settings
from app.system.equipment_workflow import resolve_equipment_workflow
from app.system.signals.duplicate import detect_duplicate_candidate


def _format_seconds(value: float) -> str:
    return f"{value:.3f}"


def _get_active_workflow_id() -> str | None:
    """Look up the active workflow id without importing configuration eagerly."""

    try:
        from app.config import config_dict
    except Exception:
        return None

    active_workflow_id = config_dict.get("active_workflow_id") if isinstance(config_dict, dict) else None
    return str(active_workflow_id) if active_workflow_id else None


def _order_test_codes(order: object) -> list[str]:
    """Extract test codes from an imported order-like object."""

    codes: list[str] = []
    for item in getattr(order, "order_items", []) or []:
        test = getattr(item, "test", None)
        code = getattr(test, "code", None)
        if code is None and isinstance(item, dict):
            test_payload = item.get("test", {})
            if isinstance(test_payload, dict):
                code = test_payload.get("code")
        if code is not None:
            codes.append(str(code))
    return codes


def _build_specimens_from_orders(orders: list[object]) -> list[Specimen]:
    """Convert order objects into one specimen per order."""

    specimens: list[Specimen] = []
    for index, order in enumerate(orders):
        order_id = getattr(order, "id", index + 1)
        pending_tests = _order_test_codes(order)
        specimen = Specimen(
            specimen_id=str(order_id),
            payload=order,
            metadata={
                "order_id": str(order_id),
                "order_priority": str(getattr(order, "priority", "")),
                "order_time": str(getattr(order, "order_datetime", "")),
                "order_source": str(getattr(order, "source", "")),
                "pending_tests": pending_tests,
                "all_tests": list(pending_tests),
            },
        )
        specimens.append(specimen)
    return specimens


def _render_progress_bar(completed: int, total: int, width: int = 24) -> str:
    """Render a compact textual progress bar."""

    if total <= 0:
        return "[........................] 0/0"

    fraction = min(1.0, max(0.0, completed / total))
    filled = min(width, int(round(fraction * width)))
    if completed >= total:
        bar = "█" * width
    else:
        bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {completed}/{total}"


def _write_progress_line(completed: int, total: int) -> None:
    """Write an updating progress line to stderr."""

    message = f"Completed orders: {_render_progress_bar(completed, total)}"
    sys.stderr.write("\r" + message)
    sys.stderr.flush()


def _finish_progress_line(completed: int, total: int) -> None:
    """Terminate the progress line cleanly."""

    sys.stderr.write("\r" + f"Completed orders: {_render_progress_bar(completed, total)}\n")
    sys.stderr.flush()


def _safe_copy(value: object) -> object:
    """Copy workflow payloads defensively without failing on odd objects."""

    if value is None:
        return None
    try:
        return deepcopy(value)
    except Exception:
        return value


@dataclass(slots=True)
class WorkflowSimulationSettings:
    """Runtime settings for a workflow simulation."""

    specimens_number: int
    random_seed: int | None = None
    realtime_factor: float | None = None
    system_variation: float = 0.10
    human_variation: float = 0.10
    interarrival_time: float = 0.0
    arrival_distribution: str = 'fixed'
    arrival_seed: int | None = None
    rejection_probability: float = 0.0
    staff_capacity: int | None = None

    @classmethod
    def from_scenario(
        cls,
        *,
        specimens_number: int,
        workflow: Workflow,
        scenario: Scenario | None = None,
        random_seed: int | None = None,
        realtime_factor: float | None = None,
    ) -> "WorkflowSimulationSettings":
        """Build settings from a workflow scenario when available."""

        scenario = scenario or workflow.active_scenario
        parameters = dict(scenario.parameters if scenario is not None else {})

        staff_capacity = parameters.get("staff_capacity")
        if scenario is not None and scenario.workforce:
            from app.system.workforce import validate_workforce
            staff_capacity = len(validate_workforce(scenario.workforce))
        if staff_capacity is not None:
            staff_capacity = int(staff_capacity)

        return cls(
            specimens_number=specimens_number,
            random_seed=random_seed,
            realtime_factor=realtime_factor,
            system_variation=float(parameters.get("system_variation", 0.10)),
            human_variation=float(parameters.get("human_variation", 0.10)),
            interarrival_time=float(parameters.get("interarrival_time", 0.0)),
            arrival_distribution=parameters.get('arrival_distribution', 'fixed'),
            arrival_seed=parameters.get('arrival_seed'),
            rejection_probability=float(parameters.get('rejection_probability', 0)),
            staff_capacity=staff_capacity,
        )


@dataclass(slots=True)
class NodeSimulationStats:
    """Aggregate metrics for a single workflow node."""

    node_id: str
    node_name: str
    specimens_processed: int = 0
    total_wait_time: float = 0.0
    total_processing_time: float = 0.0
    total_time_spent: float = 0.0
    total_staff_busy_time: float = 0.0

    def record(self, *, wait_time: float, processing_time: float, staff_busy_time: float = 0.0) -> None:
        self.specimens_processed += 1
        self.total_wait_time += wait_time
        self.total_processing_time += processing_time
        self.total_time_spent += wait_time + processing_time
        self.total_staff_busy_time += staff_busy_time

    @property
    def average_wait_time(self) -> float:
        return self.total_wait_time / self.specimens_processed if self.specimens_processed else 0.0

    @property
    def average_processing_time(self) -> float:
        return self.total_processing_time / self.specimens_processed if self.specimens_processed else 0.0

    @property
    def average_total_time(self) -> float:
        return self.total_time_spent / self.specimens_processed if self.specimens_processed else 0.0

    def utilization(self, makespan: float, staff_capacity: int | None = None) -> str:
        """Return a display-friendly utilization string."""

        if makespan <= 0:
            return "N/A"

        processing_utilization = self.total_processing_time / makespan
        if self.total_staff_busy_time <= 0 or not staff_capacity or staff_capacity <= 0:
            return f"{processing_utilization:.1%}"

        staff_utilization = self.total_staff_busy_time / (staff_capacity * makespan)
        return f"{processing_utilization:.1%} / staff {staff_utilization:.1%}"


@dataclass(slots=True)
class WorkflowSimulationResult:
    """Final result bundle returned by the simulation engine."""

    workflow_name: str
    specimens_number: int
    makespan: float
    staff_capacity: int | None = None
    node_stats: dict[str, NodeSimulationStats] = field(default_factory=dict)
    completed_specimens: list[Specimen] = field(default_factory=list)
    rejected_specimens: list[Specimen] = field(default_factory=list)
    active_node_ids: list[str] = field(default_factory=list)
    governance_consultations: list[GovernanceConsultation] = field(default_factory=list)

    def summary_rows(self, workflow: Workflow, staff_capacity: int | None = None) -> list[list[str]]:
        """Build rows for the summary table."""

        rows: list[list[str]] = []
        active_node_ids = set(self.active_node_ids)
        node_iter = (
            node
            for node in workflow.graph.iter_nodes()
            if not active_node_ids or node.node_id in active_node_ids
        )
        for node in node_iter:
            stats = self.node_stats.get(node.node_id) or NodeSimulationStats(
                node_id=node.node_id,
                node_name=node.widget.name,
            )
            rows.append(
                [
                    node.widget.name,
                    str(stats.specimens_processed),
                    _format_seconds(stats.average_wait_time),
                    _format_seconds(stats.average_processing_time),
                    _format_seconds(stats.average_total_time),
                    stats.utilization(self.makespan, staff_capacity=staff_capacity),
                ]
            )
        return rows

    def format_summary_table(self, workflow: Workflow, staff_capacity: int | None = None) -> str:
        """Render the summary as a human-readable table."""

        headers = [
            "Node",
            "Processed",
            "Avg Wait",
            "Avg Processing",
            "Avg Total",
            "Utilization",
        ]
        rows = self.summary_rows(workflow, staff_capacity=staff_capacity)

        try:
            from tabulate import tabulate
        except Exception:
            widths = [len(header) for header in headers]
            for row in [headers, *rows]:
                for index, value in enumerate(row):
                    widths[index] = max(widths[index], len(str(value)))

            def _format_row(row: list[str]) -> str:
                return " | ".join(str(value).ljust(widths[index]) for index, value in enumerate(row))

            separator = "-+-".join("-" * width for width in widths)
            return "\n".join([_format_row(headers), separator, *(_format_row(row) for row in rows)])

        return tabulate(rows, headers=headers, tablefmt="github")

    def to_dict(self) -> dict[str, object]:
        """Serialize the result bundle for export and debugging."""

        return {
            "workflow_name": self.workflow_name,
            "specimens_number": self.specimens_number,
            "makespan": self.makespan,
            "staff_capacity": self.staff_capacity,
            "node_stats": {
                node_id: {
                    "node_id": stats.node_id,
                    "node_name": stats.node_name,
                    "specimens_processed": stats.specimens_processed,
                    "total_wait_time": stats.total_wait_time,
                    "total_processing_time": stats.total_processing_time,
                    "total_time_spent": stats.total_time_spent,
                    "total_staff_busy_time": stats.total_staff_busy_time,
                }
                for node_id, stats in self.node_stats.items()
            },
            "completed_specimens": [
                {
                    "specimen_id": specimen.specimen_id,
                    "metadata": dict(specimen.metadata),
                    "history": list(specimen.history),
                }
                for specimen in self.completed_specimens
            ],
            "active_node_ids": list(self.active_node_ids),
            "governance_consultations": [consultation.to_dict() for consultation in self.governance_consultations],
        }


class WorkflowRouter:
    """Route specimens through a workflow graph."""

    def start_node_id(self, workflow: Workflow) -> str | None:
        for node in workflow.graph.iter_nodes():
            if node.widget.node_type == "order adapter" and any(
                workflow.get_node(edge.source).kind != "resource"
                for edge in workflow.graph.incoming_edges(node.node_id)
            ):
                raise ValueError("Order Adapter nodes cannot have incoming order-flow connections.")
        roots = self.start_node_ids(workflow)
        if len(roots) > 1:
            raise ValueError(
                "Workflow has multiple root nodes. Merge the flows into a single chain before simulation."
            )
        if roots:
            root = workflow.get_node(roots[0])
            if root is not None and root.widget.node_type == "order adapter" and not root.widget.enabled:
                return None
            return self.first_enabled_reachable_node_id(workflow, roots[0])
        if any(
            workflow.graph.incoming_edges(node.node_id) or workflow.graph.outgoing_edges(node.node_id)
            for node in workflow.graph.iter_nodes()
        ):
            raise ValueError(
                "Workflow has no root node. Merge the flow into a single chain before simulation."
            )
        return None

    def start_node_ids(self, workflow: Workflow) -> list[str]:
        """Return the non-orphan root nodes for the workflow."""

        active_roots: list[str] = []
        for node in workflow.graph.iter_nodes():
            if node.kind == "resource":
                continue
            incoming_flow = [
                edge for edge in workflow.graph.incoming_edges(node.node_id)
                if workflow.get_node(edge.source).kind != "resource"
            ]
            if not incoming_flow and workflow.graph.outgoing_edges(node.node_id):
                active_roots.append(node.node_id)
        return active_roots

    def first_enabled_reachable_node_id(
        self,
        workflow: Workflow,
        start_node_id: str,
        *,
        blocked_node_ids: set[str] | None = None,
    ) -> str | None:
        """Return the first enabled node reachable from ``start_node_id``."""

        if start_node_id not in workflow.graph.nodes:
            return None

        blocked = set(blocked_node_ids or set())
        seen = set(blocked)
        stack = [start_node_id]
        while stack:
            node_id = stack.pop()
            if node_id in seen or node_id not in workflow.graph.nodes:
                continue
            seen.add(node_id)
            node = workflow.graph.get_node(node_id)
            if node is not None and node.widget.enabled:
                return node_id
            for edge in reversed(workflow.graph.outgoing_edges(node_id)):
                if edge.target not in seen:
                    stack.append(edge.target)
        return None

    def reachable_node_ids(self, workflow: Workflow, start_node_id: str | None) -> list[str]:
        """Return the enabled nodes reachable from the start node."""

        if start_node_id is None:
            return []

        reachable: list[str] = []
        seen: set[str] = set()
        stack = [start_node_id]
        while stack:
            node_id = stack.pop()
            if node_id in seen or node_id not in workflow.graph.nodes:
                continue
            seen.add(node_id)
            node = workflow.graph.get_node(node_id)
            if node is not None and node.widget.enabled:
                reachable.append(node_id)
            for edge in workflow.graph.outgoing_edges(node_id):
                if edge.target not in seen:
                    stack.append(edge.target)
        return reachable

    def next_node_id(self, workflow: Workflow, current_node_id: str, specimen: Specimen) -> str | None:
        outgoing = workflow.graph.outgoing_edges(current_node_id)
        if not outgoing:
            return None
        for edge in outgoing:
            next_node_id = self.first_enabled_reachable_node_id(
                workflow,
                edge.target,
                blocked_node_ids={current_node_id},
            )
            if next_node_id is not None:
                return next_node_id
        return None


class WorkflowSimulationEngine:
    """Run a workflow as a SimPy process graph."""

    def __init__(
        self,
        workflow: Workflow,
        *,
        settings: WorkflowSimulationSettings,
        workflow_name: str | None = None,
        scenario: Scenario | None = None,
        router: WorkflowRouter | None = None,
        specimens: list[Specimen] | None = None,
        on_specimen_completed: Callable[[int, int], None] | None = None,
        on_order_created: Callable[[object], None] | None = None,
        on_activity: Callable[[dict], None] | None = None,
        governance_provider: GovernanceProvider | None = None,
        debug_governance: bool = False,
    ) -> None:
        workflow = resolve_equipment_workflow(workflow)
        self.workflow = workflow
        self.settings = settings
        self.workflow_name = workflow_name or "Untitled Workflow"
        self.scenario = scenario or workflow.active_scenario
        self.router = router or WorkflowRouter()
        self._use_equipment_pool = router is None
        self.on_specimen_completed = on_specimen_completed
        self.on_order_created = on_order_created
        self.on_activity = on_activity
        self.active_processing = {}
        if settings.realtime_factor is not None:
            self.env = simpy.rt.RealtimeEnvironment(factor=float(settings.realtime_factor), strict=False)
        else:
            self.env = simpy.Environment()
        self.rng = random.Random(settings.random_seed)
        self.completed_specimens: list[Specimen] = []
        self.rejected_specimens: list[Specimen] = []
        probability = settings.rejection_probability
        if not isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError('Specimen rejection probability must be between 0 and 1.')
        self.rejection_rng = random.Random(settings.random_seed if settings.random_seed is not None
                                           else settings.arrival_seed)
        self.governance_provider = governance_provider or SopAsCodeGovernanceProvider()
        self.governance_consultations: list[GovernanceConsultation] = []
        self.debug_governance = debug_governance
        self._observed_orders: list[object] = []
        self.specimens = (
            specimens
            if specimens is not None
            else [Specimen(specimen_id=f"specimen-{index + 1}") for index in range(settings.specimens_number)]
        )
        from app.system.arrivals import arrival_offsets
        self.arrival_offsets = arrival_offsets(len(self.specimens), settings.interarrival_time,
            settings.arrival_distribution,
            settings.random_seed if settings.random_seed is not None else settings.arrival_seed)
        self.start_node_id = self.router.start_node_id(self.workflow)
        start_node = self.workflow.get_node(self.start_node_id) if self.start_node_id else None
        self.arrival_adapter_node = (
            start_node if start_node is not None and start_node.widget.node_type == "order adapter" else None
        )
        self.arrival_adapter = (
            create_order_adapter(str(self.arrival_adapter_node.metadata.get("adapter_type", "simulation")))
            if self.arrival_adapter_node is not None else None
        )
        self.patient_adapter = patient_adapter_for_workflow(workflow) if self.arrival_adapter is not None else None
        if self.start_node_id is None:
            self.active_node_ids: list[str] = []
        else:
            self.active_node_ids = self.router.reachable_node_ids(self.workflow, self.start_node_id)
        self.equipment_resources: dict[str, simpy.Resource] = {}
        self.node_resources: dict[str, simpy.Resource] = {}
        for node in self.workflow.graph.iter_nodes():
            if node.node_id not in self.active_node_ids or node.metadata.get("terminal_output"):
                continue
            capacity = max(1, int(node.widget.capacity))
            equipment_id = str(node.metadata.get("equipment_id", "")).strip() if node.kind == "equipment" else ""
            if equipment_id:
                resource = self.equipment_resources.get(equipment_id)
                if resource is None:
                    resource = simpy.Resource(self.env, capacity=capacity)
                    self.equipment_resources[equipment_id] = resource
                elif resource.capacity != capacity:
                    raise ValueError(
                        f"Equipment {equipment_id!r} has conflicting capacities "
                        f"({resource.capacity} and {capacity}). Set the same Capacity "
                        "on all workflow nodes referencing this equipment."
                    )
            else:
                # Process nodes remain independent. Attend at is a staff
                # location assignment, not a reservation of that equipment.
                resource = simpy.Resource(self.env, capacity=capacity)
            self.node_resources[node.node_id] = resource
        # Count assignments per physical resource across all incoming routes.
        # Counts also break ties before newly scheduled requests acquire a slot.
        self._equipment_assignments: dict[simpy.Resource, int] = {}
        self.node_stats = {
            node.node_id: NodeSimulationStats(node_id=node.node_id, node_name=node.widget.name)
            for node in self.workflow.graph.iter_nodes()
            if node.node_id in self.active_node_ids
        }
        self.staff_capacity = self._resolve_staff_capacity()
        from app.system.workforce import WorkforcePool, scenario_workforce
        from app.system.model import Scenario
        roster_source = self.scenario or Scenario(name="Run")
        roster = scenario_workforce(roster_source, self.staff_capacity)
        # Run-only headcount overrides retain existing identities first.
        roster = roster[:self.staff_capacity]
        names = {member['name'].casefold() for member in roster}
        index = 1
        while len(roster) < self.staff_capacity:
            name = f'Staff {index}'
            if name.casefold() not in names:
                roster.append(dict(id=f'run-staff-{index}', name=name))
                names.add(name.casefold())
            index += 1
        self.staff_pool = WorkforcePool(self.env, roster)
        self._validate_staff_capacity()
        self._batch_queues = {}
        self._batch_wake = {}
        self._batch_sequence = 0
        for node_id in self.active_node_ids:
            staff_batch_settings(self.workflow.get_node(node_id))

    def _resolve_staff_capacity(self) -> int:
        if self.settings.staff_capacity is not None:
            return max(1, int(self.settings.staff_capacity))

        if self.scenario and self.scenario.workforce:
            return len(self.scenario.workforce)
        required_staff = []
        for node in self.workflow.graph.iter_nodes():
            if node.node_id not in self.active_node_ids:
                continue
            if int(node.widget.required_staff) > 0 or float(node.widget.human_throughput) > 0:
                required_staff.append(int(node.widget.required_staff))
        if required_staff:
            return max(1, max(required_staff))
        return 1

    def _effective_processing_capacity(self, node: WorkflowNode) -> float:
        if self.scenario and node.widget.name in self.scenario.widget_service_times:
            return float(self.scenario.widget_service_times[node.widget.name])
        if node.widget.throughput > 0:
            return float(node.widget.throughput)
        return float(node.widget.service_time)

    def _validate_staff_capacity(self) -> None:
        for node in self.workflow.graph.iter_nodes():
            if node.node_id not in self.active_node_ids:
                continue
            if node.metadata.get("terminal_output") or int(node.widget.required_staff) == 0:
                continue

            required_staff = int(node.widget.required_staff)
            if required_staff > self.staff_capacity:
                raise ValueError(
                    f"Workflow node {node.widget.name!r} requires {required_staff} staff, "
                    f"but the available staff capacity is only {self.staff_capacity}."
                )
            eligible = self._eligible_staff(node)
            if required_staff > len(eligible):
                raise ValueError(
                    f"Workflow node {node.widget.name!r} requires {required_staff} staff, "
                    f"but only {len(eligible)} eligible staff are available."
                )

    def _eligible_staff(self, node):
        from app.system.workforce import eligible_staff
        assignments = self.scenario.staff_assignments if self.scenario else {}
        try:
            return eligible_staff(self.staff_pool.roster, assignments, node.node_id)
        except ValueError as exc:
            raise ValueError(f"Workflow node {node.widget.name!r}: {exc}") from exc

    def _acquire_staff(self, node, specimen, count, **details):
        self._emit_activity("staff_waiting", node, specimen, staff_count=count, **details)
        team = yield self.staff_pool.get(count, self._eligible_staff(node))
        self._emit_activity("staff_acquired", node, specimen, staff_count=len(team),
                            staff_ids=[m['id'] for m in team],
                            staff_names=[m['name'] for m in team], **details)
        return team

    def _release_staff(self, node, specimen, team, **details):
        self._emit_activity("staff_released", node, specimen, staff_count=len(team),
                            staff_ids=[m['id'] for m in team],
                            staff_names=[m['name'] for m in team], **details)
        # WorkforcePool.put releases the team immediately. Cleanup must not
        # yield: Python closes suspended generators with GeneratorExit.
        self.staff_pool.put(team)

    def _effective_human_processing_capacity(self, node: WorkflowNode) -> float:
        return float(node.widget.human_throughput)

    def _sample_service_time(self, capacity: float, variation: float) -> float:
        if capacity <= 0:
            return 0.0
        base_time = 1.0 / capacity
        multiplier = self.rng.uniform(max(0.0, 1.0 - variation), 1.0 + variation)
        return max(0.0, base_time * multiplier)

    def _node_requires_human_attention(self, node: WorkflowNode) -> bool:
        return (int(node.widget.required_staff) > 0
                and self._effective_human_processing_capacity(node) > 0)

    def _specimen_pending_tests(self, specimen: Specimen) -> list[str]:
        pending_tests = specimen.metadata.get("pending_tests", [])
        if isinstance(pending_tests, list):
            return [str(code) for code in pending_tests]
        return []

    def _set_specimen_pending_tests(self, specimen: Specimen, pending_tests: list[str]) -> None:
        specimen.metadata["pending_tests"] = list(pending_tests)

    def _node_uses_duplicate_detection(self, node: WorkflowNode) -> bool:
        """Return whether this node should run the duplicate-order adapter."""

        if node.widget.node_type in {"Evaluation", "AI agent"}:
            return True
        return node.metadata.get("plugin") == "duplicate_order_detector"

    def _build_duplicate_detection_context(self, specimen: Specimen) -> tuple[object | None, list[object]]:
        """Build the order and prior-order context required by the duplicate detector."""

        arrival_index = int(specimen.metadata.get("arrival_index", len(self._observed_orders)))
        prior_orders = [
            order
            for order in self._observed_orders[:arrival_index]
            if order is not None
        ]
        return specimen.payload, prior_orders

    def _run_duplicate_detection_adapter(self, *, node: WorkflowNode, specimen: Specimen) -> bool:
        """Run the temporary duplicate-order adapter for evaluation nodes."""

        current_order, prior_orders = self._build_duplicate_detection_context(specimen)
        specimen_id = specimen.specimen_id
        order_id = self._order_id_for_specimen(specimen) or str(specimen.metadata.get("order_id") or "")
        detection_run = current_order is not None
        duplicate_candidate = False
        consultation_created = False

        if detection_run:
            duplicate_candidate = detect_duplicate_candidate(current_order, prior_orders)

        if duplicate_candidate:
            governance_event = GovernanceEvent(
                event_type=EVENT_DUPLICATE_ORDER_DETECTED,
                node_id=node.node_id,
                order_id=self._order_id_for_specimen(specimen),
                specimen_id=specimen.specimen_id,
                sim_time=self.env.now,
                context={
                    "order": current_order,
                    "prior_orders": prior_orders,
                    "duplicate_candidate": True,
                    "matched_action": "REQUEST_REVIEW",
                    "node_name": node.widget.name,
                    "node_type": node.widget.node_type,
                    "workflow_name": self.workflow_name,
                },
            )
            self._consult_governance_event(governance_event, specimen=specimen)
            consultation_created = True

        self._debug_duplicate_detection(
            node_id=node.node_id,
            specimen_id=specimen_id,
            order_id=order_id or "-",
            detection_run=detection_run,
            duplicate_candidate=duplicate_candidate,
            consultation_created=consultation_created,
        )
        return duplicate_candidate

    def _debug_duplicate_detection(
        self,
        *,
        node_id: str,
        specimen_id: str,
        order_id: str,
        detection_run: bool,
        duplicate_candidate: bool,
        consultation_created: bool,
    ) -> None:
        """Print temporary adapter diagnostics when debug output is enabled."""

        if not self.debug_governance:
            return
        print(
            "Governance debug: "
            f"node_id={node_id} "
            f"specimen_id={specimen_id} "
            f"order_id={order_id} "
            f"detection_run={detection_run} "
            f"duplicate_candidate={duplicate_candidate} "
            f"consultation_created={consultation_created}"
        )

    def _consult_governance_event(
        self,
        governance_event: GovernanceEvent,
        *,
        specimen: Specimen | None = None,
    ) -> Decision:
        """Evaluate and log one governance event."""

        decision = self.governance_provider.evaluate(governance_event)
        consultation = GovernanceConsultation(
            sim_time=governance_event.sim_time,
            event_type=governance_event.event_type,
            node_id=governance_event.node_id,
            order_id=governance_event.order_id,
            specimen_id=governance_event.specimen_id,
            provider_name=self.governance_provider.provider_name,
            decision_action=decision.action,
            requires_human=decision.requires_human,
            reason=decision.reason,
            metadata={
                **deepcopy(decision.metadata),
                "target_node": decision.target_node,
            },
        )
        self.governance_consultations.append(consultation)
        specimen = specimen or governance_event.context.get("specimen")
        specimen_metadata = getattr(specimen, "metadata", None)
        if isinstance(specimen_metadata, dict):
            consultation_log = specimen_metadata.get("governance_consultations")
            if not isinstance(consultation_log, list):
                consultation_log = []
                specimen_metadata["governance_consultations"] = consultation_log
            consultation_log.append(consultation.to_dict())
        return decision

    def _governance_events_for_node(self, node: WorkflowNode) -> list[dict[str, object]]:
        """Return governance event declarations attached to a workflow node."""

        declared_events = node.metadata.get("governance_events")
        if isinstance(declared_events, list) and declared_events:
            events: list[dict[str, object]] = []
            for item in declared_events:
                if isinstance(item, str):
                    events.append({"event_type": item.upper()})
                elif isinstance(item, dict) and item.get("event_type"):
                    normalized_item = dict(item)
                    normalized_item["event_type"] = str(normalized_item["event_type"]).upper()
                    events.append(normalized_item)
            if events:
                return events

        if node.widget.node_type == "specimen registration":
            return [
                {"event_type": "ORDER_RECEIVED"},
                {"event_type": "DUPLICATE_ORDER_CHECK"},
            ]

        return []

    def _order_id_for_specimen(self, specimen: Specimen) -> str | None:
        order_id = specimen.metadata.get("order_id")
        if order_id is not None:
            return str(order_id)

        payload = specimen.payload
        if payload is not None and hasattr(payload, "id"):
            value = getattr(payload, "id")
            if value is not None:
                return str(value)
        return None

    def _governance_context(
        self,
        *,
        node: WorkflowNode,
        specimen: Specimen,
        governance_event: dict[str, object],
    ) -> dict[str, object]:
        """Build the compact context passed to the governance provider."""

        arrival_index = int(specimen.metadata.get("arrival_index", len(self._observed_orders)))
        prior_orders = [
            order
            for order in self._observed_orders[:arrival_index]
            if order is not None
        ]
        context: dict[str, object] = {
            "workflow_name": self.workflow_name,
            "node_name": node.widget.name,
            "node_type": node.widget.node_type,
            "specimen": specimen,
            "order": specimen.payload,
            "payload": specimen.payload if isinstance(specimen.payload, dict) else _safe_copy(specimen.payload),
            "prior_orders": prior_orders,
        }
        governance_context = node.metadata.get("governance_context", {})
        if isinstance(governance_context, dict):
            context.update(governance_context)

        for key in ("target_node", "matched_action", "human_review_status", "patient_id", "test_code"):
            if key in governance_event and governance_event[key] is not None:
                context[key] = governance_event[key]

        if "payload" not in context or context["payload"] is None:
            context["payload"] = specimen.metadata.get("governance_payload", {})
        return context

    def _consult_governance(
        self,
        *,
        node: WorkflowNode,
        specimen: Specimen,
        record_completion: bool = True,
    ) -> tuple[bool, str | None]:
        """Consult governance events attached to the current node."""

        next_node_override: str | None = None
        for governance_event_config in self._governance_events_for_node(node):
            event_type = str(governance_event_config.get("event_type", "")).strip().upper()
            if not event_type:
                continue

            governance_event = GovernanceEvent(
                event_type=event_type,
                node_id=node.node_id,
                order_id=self._order_id_for_specimen(specimen),
                specimen_id=specimen.specimen_id,
                sim_time=self.env.now,
                context=self._governance_context(
                    node=node,
                    specimen=specimen,
                    governance_event=governance_event_config,
                ),
            )
            governance_event.context["specimen"] = specimen
            decision = self._consult_governance_event(governance_event)

            if (
                decision.target_node
                and decision.target_node != node.node_id
                and decision.target_node in self.workflow.graph.nodes
            ):
                next_node_override = decision.target_node

            if decision.action == ACTION_REJECT_DUPLICATE:
                specimen.record_event(
                    node.widget.name,
                    "governance_rejected",
                    action=decision.action,
                    reason=decision.reason,
                    time=self.env.now,
                )
                if record_completion:
                    self.completed_specimens.append(specimen)
                    if self.on_specimen_completed is not None:
                        self.on_specimen_completed(len(self.completed_specimens), len(self.specimens))
                return True, next_node_override

        return False, next_node_override

    def _processing_timeout(self, node, specimen, duration, phase):
        """Expose the actual sampled service interval without changing its timing."""
        key = (node.node_id, specimen.specimen_id)
        self.active_processing[key] = dict(start=self.env.now, end=self.env.now + duration,
                                           phase=phase)
        try:
            yield self.env.timeout(duration)
        finally:
            self.active_processing.pop(key, None)

    def _process_analyzer_node(
        self,
        *,
        node: WorkflowNode,
        specimen: Specimen,
        node_wait_time: float,
    ) -> float:
        """Process all analyzable pending tests for one specimen at an analyzer node."""

        if "pending_tests" not in specimen.metadata:
            human_wait_time = 0.0
            human_processing_time = 0.0
            staff_busy_time = 0.0

            system_processing_time = self._sample_service_time(
                self._effective_processing_capacity(node),
                self.settings.system_variation,
            )
            yield from self._processing_timeout(node, specimen, system_processing_time, 'Processing')
            specimen.record_event(
                node.widget.name,
                "system_finished",
                processing_time=system_processing_time,
                time=self.env.now,
            )

            if self._node_requires_human_attention(node):
                required_staff = max(1, int(node.widget.required_staff))
                human_wait_start = self.env.now
                team = yield from self._acquire_staff(node, specimen, required_staff)
                human_wait_time = self.env.now - human_wait_start
                specimen.record_event(
                    node.widget.name,
                    "human_started",
                    wait_time=human_wait_time,
                    staff_required=required_staff,
                    time=self.env.now,
                )

                human_processing_time = self._sample_service_time(
                    self._effective_human_processing_capacity(node),
                    self.settings.human_variation,
                )
                staff_busy_time = human_processing_time * required_staff
                yield from self._processing_timeout(node, specimen, human_processing_time, 'Staff work')
                self._release_staff(node, specimen, team)
                specimen.record_event(
                    node.widget.name,
                    "human_finished",
                    processing_time=human_processing_time,
                    staff_required=required_staff,
                    time=self.env.now,
                )

            total_wait_time = node_wait_time + human_wait_time
            total_processing_time = system_processing_time + human_processing_time
            self.node_stats[node.node_id].record(
                wait_time=total_wait_time,
                processing_time=total_processing_time,
                staff_busy_time=staff_busy_time,
            )
            return total_processing_time

        analyzer_tests = {
            str(test_code)
            for test_code in node.widget.metadata.get("analyzer_tests", [])
            if str(test_code).strip()
        }
        pending_tests = self._specimen_pending_tests(specimen)
        processable_tests = [
            test_code
            for test_code in pending_tests
            if not analyzer_tests or test_code in analyzer_tests
        ]

        if not processable_tests:
            self.node_stats[node.node_id].record(
                wait_time=node_wait_time,
                processing_time=0.0,
                staff_busy_time=0.0,
            )
            specimen.record_event(
                node.widget.name,
                "analyzer_skipped",
                wait_time=node_wait_time,
                time=self.env.now,
            )
            return 0.0

        processing_variation = self.settings.system_variation
        processing_capacity = self._effective_processing_capacity(node)
        if node.widget.node_type == "manual analyzer":
            processing_variation = self.settings.human_variation
            processing_capacity = self._effective_human_processing_capacity(node)

        if processing_capacity <= 0:
            self.node_stats[node.node_id].record(
                wait_time=node_wait_time,
                processing_time=0.0,
                staff_busy_time=0.0,
            )
            return 0.0

        staff_wait_time = 0.0
        staff_busy_time = 0.0
        acquired_staff = 0
        if node.widget.node_type == "manual analyzer" and int(node.widget.required_staff) > 0:
            acquired_staff = max(1, int(node.widget.required_staff))
            staff_wait_start = self.env.now
            team = yield from self._acquire_staff(node, specimen, acquired_staff)
            staff_wait_time = self.env.now - staff_wait_start
            specimen.record_event(
                node.widget.name,
                "human_started",
                wait_time=staff_wait_time,
                staff_required=acquired_staff,
                time=self.env.now,
            )

        total_processing_time = 0.0
        try:
            for test_code in processable_tests:
                test_processing_time = self._sample_service_time(processing_capacity, processing_variation)
                specimen.record_event(
                    node.widget.name,
                    "test_started",
                    test_code=test_code,
                    time=self.env.now,
                )
                yield from self._processing_timeout(node, specimen, test_processing_time, 'Testing')
                total_processing_time += test_processing_time
                specimen.record_event(
                    node.widget.name,
                    "test_finished",
                    test_code=test_code,
                    processing_time=test_processing_time,
                    time=self.env.now,
                )
        finally:
            if node.widget.node_type == "manual analyzer" and acquired_staff > 0:
                staff_busy_time = total_processing_time * acquired_staff
                self._release_staff(node, specimen, team)
                specimen.record_event(
                    node.widget.name,
                    "human_finished",
                    processing_time=total_processing_time,
                    staff_required=acquired_staff,
                    time=self.env.now,
                )

        if node.widget.node_type == "automated analyzer" and self._node_requires_human_attention(node):
            required = int(node.widget.required_staff)
            wait_start = self.env.now
            team = yield from self._acquire_staff(node, specimen, required)
            staff_wait_time += self.env.now - wait_start
            human_time = self._sample_service_time(self._effective_human_processing_capacity(node),
                                                  self.settings.human_variation)
            specimen.record_event(node.widget.name, "human_started", time=self.env.now,
                                  staff_required=required, wait_time=self.env.now - wait_start)
            try:
                yield from self._processing_timeout(node, specimen, human_time, 'Staff work')
                total_processing_time += human_time
                staff_busy_time += human_time * required
            finally:
                self._release_staff(node, specimen, team)
            specimen.record_event(node.widget.name, "human_finished", time=self.env.now,
                                  staff_required=required, processing_time=human_time)

        completed = specimen.metadata.setdefault("completed_tests", [])
        completed.extend(code for code in processable_tests if code not in completed)
        remaining_tests = [test_code for test_code in pending_tests if test_code not in processable_tests]
        self._set_specimen_pending_tests(specimen, remaining_tests)
        self.node_stats[node.node_id].record(
            wait_time=node_wait_time + staff_wait_time,
            processing_time=total_processing_time,
            staff_busy_time=staff_busy_time,
        )
        return total_processing_time

    @staticmethod
    def _is_reporting_node(node: WorkflowNode) -> bool:
        return bool(node.metadata.get("terminal_output")) or node.widget.node_type.lower() in {
            "result reporting", "reporting", "result validating", "validating",
        } or node.widget.name.strip().lower() in {"reporting", "reporting station", "validating"}

    def _require_complete_order(self, specimen: Specimen, node_name: str) -> None:
        pending = self._specimen_pending_tests(specimen)
        required = set(str(code) for code in specimen.metadata.get("all_tests", []))
        completed = set(specimen.metadata.get("completed_tests", []))
        missing = sorted(set(pending) | (required - completed))
        if missing:
            order_id = specimen.metadata.get("order_id", specimen.specimen_id)
            specimen.record_event(node_name, "report_blocked", order_id=order_id,
                                  pending_tests=missing, time=self.env.now)
            raise ValueError(f"Order {order_id}: cannot report or complete with unfinished tests: {', '.join(missing)}")

    def _analyzer_pool_after(self, source_id: str):
        """Recognize direct analyzer alternatives that converge on one next step."""
        if not self._use_equipment_pool:
            return None
        edges = self.workflow.graph.outgoing_edges(source_id)
        if len(edges) < 2:
            return None
        nodes = []
        joins = set()
        for edge in edges:
            node = self.workflow.get_node(edge.target)
            if node is None or node.widget.node_type not in {"automated analyzer", "manual analyzer"}:
                return None
            outgoing = self.workflow.graph.outgoing_edges(node.node_id)
            if len(outgoing) != 1:
                return None
            joins.add(outgoing[0].target)
            if node.widget.enabled:
                nodes.append(node)
        if not nodes or len(joins) != 1:
            return None
        return nodes, next(iter(joins))

    def _advance_specimen(self, source_id: str, specimen: Specimen):
        pool = self._analyzer_pool_after(source_id)
        if pool is None or "pending_tests" not in specimen.metadata:
            return self._next_node_id(source_id, specimen)
        nodes, join_id = pool
        assignments = {node.node_id: [] for node in nodes}
        projected = {}
        for node in nodes:
            resource = self.node_resources[node.node_id]
            capacity = (self._effective_human_processing_capacity(node)
                        if node.widget.node_type == "manual analyzer"
                        else self._effective_processing_capacity(node))
            projected[node.node_id] = ((resource.count + len(resource.queue)) / resource.capacity,
                                       1 / capacity if capacity > 0 else None)
        for code in dict.fromkeys(self._specimen_pending_tests(specimen)):
            compatible = [node for node in nodes
                          if projected[node.node_id][1] is not None
                          and (not node.widget.metadata.get("analyzer_tests")
                               or code in {str(test) for test in node.widget.metadata["analyzer_tests"]})]
            if not compatible:
                order_id = specimen.metadata.get("order_id", specimen.specimen_id)
                raise ValueError(f"Order {order_id}: no enabled analyzer with processing capacity supports test {code}")
            def assignment_priority(node):
                resource = self.node_resources[node.node_id]
                backlog, duration = projected[node.node_id]
                planned = sum(len(assignments[other.node_id]) for other in nodes
                              if self.node_resources[other.node_id] is resource)
                load = backlog + planned / resource.capacity
                return (load, self._equipment_assignments.get(resource, 0),
                        (load + 1) * duration)
            selected = min(compatible, key=assignment_priority)
            resource = self.node_resources[selected.node_id]
            self._equipment_assignments[resource] = self._equipment_assignments.get(resource, 0) + 1
            assignments[selected.node_id].append(code)

        branches = []
        processes = []
        for node in nodes:
            tests = assignments[node.node_id]
            if not tests:
                continue
            branch = Specimen(specimen.specimen_id, payload=specimen.payload,
                metadata={**specimen.metadata, "pending_tests": list(tests),
                          "all_tests": list(tests), "completed_tests": []},
                history=specimen.history)
            branches.append(branch)
            specimen.record_event("workflow", "tests_assigned", node_id=node.node_id,
                                  tests=list(tests), time=self.env.now)
            processes.append(self.env.process(self._specimen_process(
                -1, branch_specimen=branch, start_node_id=node.node_id, stop_node_id=join_id)))
        if processes:
            yield self.env.all_of(processes)
        specimen.metadata["last_equipment_ids"] = list(dict.fromkeys(
            equipment_id for branch in branches
            for equipment_id in branch.metadata.get("last_equipment_ids", [])))
        completed = specimen.metadata.setdefault("completed_tests", [])
        for branch in branches:
            completed.extend(code for code in branch.metadata["completed_tests"] if code not in completed)
        self._set_specimen_pending_tests(specimen, [code for code in self._specimen_pending_tests(specimen)
                                                  if code not in completed])
        specimen.record_event("workflow", "tests_joined", pending_tests=self._specimen_pending_tests(specimen),
                              time=self.env.now)
        return join_id

    def _next_node_id(self, current_node_id: str, specimen: Specimen) -> str | None:
        """Balance equivalent equipment alternatives; preserve explicit custom routing."""

        fallback = self.router.next_node_id(self.workflow, current_node_id, specimen)
        if not self._use_equipment_pool:
            return fallback
        candidates = []
        for edge in self.workflow.graph.outgoing_edges(current_node_id):
            node = self.workflow.get_node(edge.target)
            # Only direct equipment alternatives form a pool. Disabled-node
            # bypass and task/decision branches retain the router's semantics.
            if node is None or node.kind != "equipment" or node.metadata.get("terminal_output"):
                return fallback
            if node.widget.enabled:
                candidates.append(node)
        if len(candidates) < 2:
            return fallback
        signatures = {
            (node.widget.node_type, tuple(sorted(str(code) for code in
                node.widget.metadata.get("analyzer_tests", []))))
            for node in candidates
        }
        if len(signatures) != 1:
            return fallback

        def workload(node: WorkflowNode) -> float:
            resource = self.node_resources[node.node_id]
            processing = self._effective_processing_capacity(node)
            human = self._effective_human_processing_capacity(node)
            duration = (1 / processing if processing > 0 else 0) + (1 / human if human > 0 else 0)
            if node.widget.node_type in {"automated analyzer", "manual analyzer"}:
                if "pending_tests" in specimen.metadata:
                    supported = set(node.widget.metadata.get("analyzer_tests", []))
                    tests = self._specimen_pending_tests(specimen)
                    duration *= sum(not supported or code in supported for code in tests)
            # Requests already include active jobs and queued work, including
            # other workflow nodes using the same physical equipment ID.
            waves = (resource.count + len(resource.queue)) // resource.capacity + 1
            return waves * duration

        def selection_priority(node):
            resource = self.node_resources[node.node_id]
            load = (resource.count + len(resource.queue)) / resource.capacity
            return load, self._equipment_assignments.get(resource, 0), workload(node)

        selected = min(candidates, key=selection_priority)
        resource = self.node_resources[selected.node_id]
        self._equipment_assignments[resource] = self._equipment_assignments.get(resource, 0) + 1
        specimen.record_event("workflow", "equipment_routed", node_id=selected.node_id,
                              from_node_id=current_node_id, time=self.env.now)
        return selected.node_id

    def _emit_activity(self, event: str, node: WorkflowNode, specimen: Specimen, **details) -> None:
        if event == "arrived":
            details["completed_tests"] = list(specimen.metadata.get("completed_tests", []))
            details["order_id"] = str(specimen.metadata.get("order_id", specimen.specimen_id))
            details["source_equipment_ids"] = list(specimen.metadata.get("last_equipment_ids", []))
            if node.kind == "equipment" and node.metadata.get("equipment_id"):
                specimen.metadata["last_equipment_ids"] = [str(node.metadata["equipment_id"])]
        if self.on_activity is not None:
            self.on_activity({"event": event, "node_id": node.node_id,
                              "specimen_id": specimen.specimen_id, "time": float(self.env.now), **details})

    def _queue_staff_batch(self, node, specimen):
        """Join a preparation queue without reserving equipment or staff."""
        if node.node_id not in self._batch_queues:
            self._batch_queues[node.node_id] = []
            self._batch_wake[node.node_id] = self.env.event()
            self.env.process(self._collect_staff_batches(node))
        done = self.env.event()
        self._batch_queues[node.node_id].append((specimen, done, self.env.now))
        wake = self._batch_wake[node.node_id]
        if not wake.triggered:
            wake.succeed()
        return done

    def _collect_staff_batches(self, node):
        size, wait_minutes = staff_batch_settings(node)
        queue = self._batch_queues[node.node_id]
        while True:
            if not queue:
                if self._batch_wake[node.node_id].triggered:
                    self._batch_wake[node.node_id] = self.env.event()
                yield self._batch_wake[node.node_id]
            deadline = queue[0][2] + wait_minutes / 60
            while len(queue) < size and self.env.now < deadline:
                if self._batch_wake[node.node_id].triggered:
                    self._batch_wake[node.node_id] = self.env.event()
                yield self._batch_wake[node.node_id] | self.env.timeout(deadline - self.env.now)
            members = queue[:size]
            del queue[:size]
            self._batch_sequence += 1
            batch_id = f"{node.node_id}-batch-{self._batch_sequence}"
            # Node Capacity limits concurrent batches; each batch processes its
            # specimens sequentially, so it never requires extra machine slots.
            self.env.process(self._process_staff_batch(node, members, batch_id))

    def _process_staff_batch(self, node, members, batch_id):
        representative = members[0][0]
        processed = []
        batch_details = {"batch_id": batch_id, "batch_size": len(members),
                         "specimen_ids": [member[0].specimen_id for member in members]}
        for specimen, _, _ in members:
            specimen.record_event(node.widget.name, "batch_formed", time=self.env.now, **batch_details)
        with self.node_resources[node.node_id].request() as request:
            yield request
            required = max(1, int(node.widget.required_staff))
            team = yield from self._acquire_staff(node, representative, required, **batch_details)
            try:
                # Capacity remains per item. Batching shares one staff
                # allocation and releases the prepared group together.
                for specimen, _, arrived in members:
                    started = self.env.now
                    wait = started - arrived
                    specimen.record_event(node.widget.name, "system_started", wait_time=wait,
                                          time=started, **batch_details)
                    self._emit_activity("started", node, specimen, **batch_details)
                    system_time = self._sample_service_time(self._effective_processing_capacity(node),
                                                           self.settings.system_variation)
                    yield from self._processing_timeout(node, specimen, system_time, 'Processing')
                    specimen.record_event(node.widget.name, "system_finished", processing_time=system_time,
                                          time=self.env.now, **batch_details)
                    specimen.record_event(node.widget.name, "human_started", staff_required=required,
                                          time=self.env.now, **batch_details)
                    human_time = self._sample_service_time(self._effective_human_processing_capacity(node),
                                                          self.settings.human_variation)
                    yield from self._processing_timeout(node, specimen, human_time, 'Staff work')
                    specimen.record_event(node.widget.name, "human_finished", processing_time=human_time,
                                          staff_required=required, time=self.env.now, **batch_details)
                    duration = system_time + human_time
                    processed.append((specimen, arrived, duration))
                    specimen.record_event(node.widget.name, "preparation_finished", time=self.env.now, **batch_details)
                    self._emit_activity("finished", node, specimen, **batch_details)
            finally:
                self._release_staff(node, representative, team, **batch_details)
        for specimen, arrived, duration in processed:
            wait = self.env.now - arrived - duration
            self.node_stats[node.node_id].record(wait_time=wait, processing_time=duration,
                                                staff_busy_time=duration * required)
            specimen.record_event(node.widget.name, "departed", wait_time=wait,
                                  processing_time=duration, time=self.env.now, **batch_details)
        for specimen, done, _ in members:
            specimen.record_event(node.widget.name, "batch_released", time=self.env.now, **batch_details)
            done.succeed()

    def _specimen_process(self, specimen_index: int, *, branch_specimen: Specimen | None = None,
                          start_node_id: str | None = None, stop_node_id: str | None = None):
        specimen = branch_specimen if branch_specimen is not None else self.specimens[specimen_index]
        if branch_specimen is not None:
            current_node_id = start_node_id
        elif specimen.metadata.get('entry_node_id'):
            current_node_id = specimen.metadata['entry_node_id']
            if current_node_id not in self.active_node_ids:
                raise ValueError('Specimen entry node must be reachable in the active workflow.')
            if specimen.metadata.get('skip_entry_node'):
                current_node_id = yield from self._advance_specimen(current_node_id, specimen)
        elif self.arrival_adapter_node is not None:
            current_node_id = yield from self._advance_specimen(self.arrival_adapter_node.node_id, specimen)
        else:
            current_node_id = self.start_node_id
        if current_node_id is None:
            return

        while current_node_id is not None:
            if current_node_id == stop_node_id:
                return
            node = self.workflow.graph.get_node(current_node_id)
            if node is None:
                break
            if not node.widget.enabled:
                current_node_id = self._next_node_id(node.node_id, specimen)
                continue

            if self._is_reporting_node(node):
                self._require_complete_order(specimen, node.widget.name)
            specimen.record_event(node.widget.name, "arrived", time=self.env.now)
            self._emit_activity("arrived", node, specimen)
            if node.metadata.get("terminal_output"):
                # The floor reporting station visualizes completion only.
                specimen.record_event(node.widget.name, "report_received", time=self.env.now)
                self._emit_activity("completed", node, specimen)
                break
            if self._node_uses_duplicate_detection(node):
                self._run_duplicate_detection_adapter(node=node, specimen=specimen)
                self._emit_activity("finished", node, specimen)
                current_node_id = self._next_node_id(node.node_id, specimen)
                continue
            governance_stopped, next_node_override = self._consult_governance(
                node=node, specimen=specimen, record_completion=branch_specimen is None)
            if governance_stopped:
                self._emit_activity("finished", node, specimen)
                return
            if staff_batch_settings(node)[0] > 1:
                yield self._queue_staff_batch(node, specimen)
                if next_node_override:
                    current_node_id = next_node_override
                else:
                    current_node_id = yield from self._advance_specimen(node.node_id, specimen)
                continue
            node_wait_start = self.env.now
            with self.node_resources[node.node_id].request() as node_request:
                yield node_request
                node_wait_time = self.env.now - node_wait_start
                specimen.record_event(node.widget.name, "system_started", wait_time=node_wait_time, time=self.env.now)
                self._emit_activity("started", node, specimen)

                # With no separate human rate, Required staff reserves a team
                # for the node's processing duration instead.
                whole_job_team = []
                whole_job_wait = 0.0
                if int(node.widget.required_staff) > 0 and self._effective_human_processing_capacity(node) <= 0:
                    staff_wait_start = self.env.now
                    whole_job_team = yield from self._acquire_staff(node, specimen, int(node.widget.required_staff))
                    whole_job_wait = self.env.now - staff_wait_start
                    whole_job_start = self.env.now

                if node.widget.node_type in {"automated analyzer", "manual analyzer"}:
                    total_processing_time = yield from self._process_analyzer_node(
                        node=node,
                        specimen=specimen,
                        node_wait_time=node_wait_time,
                    )
                    specimen.record_event(
                        node.widget.name,
                        "departed",
                        wait_time=node_wait_time,
                        processing_time=total_processing_time,
                        time=self.env.now,
                    )
                else:
                    human_wait_time = 0.0
                    human_processing_time = 0.0
                    staff_busy_time = 0.0

                    system_processing_time = self._sample_service_time(
                        self._effective_processing_capacity(node),
                        self.settings.system_variation,
                    )
                    yield from self._processing_timeout(node, specimen, system_processing_time, 'Processing')
                    specimen.record_event(
                        node.widget.name,
                        "system_finished",
                        processing_time=system_processing_time,
                        time=self.env.now,
                    )

                    if self._node_requires_human_attention(node):
                        required_staff = max(1, int(node.widget.required_staff))
                        human_wait_start = self.env.now
                        team = yield from self._acquire_staff(node, specimen, required_staff)
                        human_wait_time = self.env.now - human_wait_start
                        specimen.record_event(
                            node.widget.name,
                            "human_started",
                            wait_time=human_wait_time,
                            staff_required=required_staff,
                            time=self.env.now,
                        )

                        human_processing_time = self._sample_service_time(
                            self._effective_human_processing_capacity(node),
                            self.settings.human_variation,
                        )
                        staff_busy_time = human_processing_time * required_staff
                        yield from self._processing_timeout(node, specimen, human_processing_time, 'Staff work')
                        self._release_staff(node, specimen, team)
                        specimen.record_event(
                            node.widget.name,
                            "human_finished",
                            processing_time=human_processing_time,
                            staff_required=required_staff,
                            time=self.env.now,
                        )

                    total_wait_time = node_wait_time + human_wait_time
                    total_processing_time = system_processing_time + human_processing_time
                    self.node_stats[node.node_id].record(
                        wait_time=total_wait_time,
                        processing_time=total_processing_time,
                        staff_busy_time=staff_busy_time,
                    )
                    specimen.record_event(
                        node.widget.name,
                        "departed",
                        wait_time=total_wait_time,
                        processing_time=total_processing_time,
                        time=self.env.now,
                    )

                if whole_job_team:
                    stats = self.node_stats[node.node_id]
                    stats.total_staff_busy_time += (self.env.now - whole_job_start) * len(whole_job_team)
                    stats.total_wait_time += whole_job_wait
                    stats.total_time_spent += whole_job_wait
                    self._release_staff(node, specimen, whole_job_team)

            self._emit_activity("finished", node, specimen)
            if (node.widget.node_type == 'specimen registration'
                    and not specimen.metadata.get('reception_checked')):
                specimen.metadata['reception_checked'] = True
                if (self.settings.rejection_probability > 0
                        and self.rejection_rng.random() < self.settings.rejection_probability):
                    reason = 'Simulated specimen quality rejection'
                    specimen.metadata.update(rejected=True, rejected_at=float(self.env.now), rejection_reason=reason)
                    if isinstance(getattr(specimen.payload, 'metadata', None), dict):
                        specimen.payload.metadata.update(rejected=True, rejected_at=float(self.env.now), rejection_reason=reason)
                    specimen.record_event(node.widget.name, 'specimen_rejected', time=self.env.now, reason=reason)
                    self.rejected_specimens.append(specimen)
                    return
            if next_node_override:
                current_node_id = next_node_override
            else:
                current_node_id = yield from self._advance_specimen(node.node_id, specimen)

        if branch_specimen is not None:
            return
        self._require_complete_order(specimen, "workflow")
        specimen.record_event("workflow", "completed", time=self.env.now)
        self.completed_specimens.append(specimen)
        if self.on_specimen_completed is not None:
            self.on_specimen_completed(len(self.completed_specimens), len(self.specimens))

    def _arrival_process(self):
        processes = []
        for specimen_index in range(len(self.specimens)):
            specimen = self.specimens[specimen_index]
            if self.arrival_adapter is not None and specimen.payload is None:
                order = self.arrival_adapter.create_order(
                    specimen_index + 1,
                    adapter_id=self.arrival_adapter_node.node_id,
                    simulation_time=float(self.env.now),
                    patient=self.patient_adapter.patient_for_order(specimen_index + 1),
                )
                # Generated orders need actual test work to exercise analyzers.
                # Imported orders retain their original requested tests.
                analyzers = [self.workflow.get_node(node_id) for node_id in self.active_node_ids
                             if self.workflow.get_node(node_id).widget.node_type
                             in {"automated analyzer", "manual analyzer"}]
                if analyzers and not order.order_items:
                    codes = sorted({str(code) for node in analyzers
                                    for code in node.widget.metadata.get("analyzer_tests", [])
                                    if str(code).strip()}) or ["SIM-TEST"]
                    order.order_items = [InMemoryOrderItem(InMemoryTest(code)) for code in codes]
                if self.on_order_created is not None:
                    self.on_order_created(order)
                specimen = _build_specimens_from_orders([order])[0]
                specimen.record_event(
                    self.arrival_adapter_node.widget.name,
                    "order_created",
                    order_id=str(order.id), source=str(order.source),
                    adapter_id=self.arrival_adapter_node.node_id, time=self.env.now,
                )
                self.specimens[specimen_index] = specimen
            specimen.metadata["arrival_index"] = specimen_index
            self._observed_orders.append(specimen.payload)
            processes.append(self.env.process(self._specimen_process(specimen_index)))
            if specimen_index + 1 < len(self.specimens):
                gap = self.arrival_offsets[specimen_index + 1] - self.arrival_offsets[specimen_index]
                if gap > 0:
                    yield self.env.timeout(gap)
        yield self.env.all_of(processes)

    def run(self) -> WorkflowSimulationResult:
        """Execute the simulation and return a summary."""

        if not self.workflow.graph.nodes or not self.active_node_ids:
            return WorkflowSimulationResult(
                workflow_name=self.workflow_name,
                specimens_number=len(self.specimens),
                makespan=0.0,
                staff_capacity=self.staff_capacity,
                active_node_ids=self.active_node_ids,
                governance_consultations=self.governance_consultations,
            )

        arrivals = self.env.process(self._arrival_process())
        self.env.run(until=arrivals)

        return self.result_summary()

    def result_summary(self) -> WorkflowSimulationResult:
        """Summarize the current engine without starting another arrival process."""
        return WorkflowSimulationResult(
            workflow_name=self.workflow_name,
            specimens_number=len(self.specimens),
            makespan=self.env.now,
            staff_capacity=self.staff_capacity,
            node_stats=self.node_stats,
            completed_specimens=self.completed_specimens,
            rejected_specimens=self.rejected_specimens,
            active_node_ids=self.active_node_ids,
            governance_consultations=self.governance_consultations,
        )


def load_workflow_document_for_simulation(
    repository: WorkflowRepository | None = None,
    *,
    workflow_id: str | None = None,
) -> WorkflowDocument:
    """Resolve the workflow document that should be simulated."""

    repository = repository or WorkflowRepository()
    if workflow_id:
        return repository.load(workflow_id)

    active_workflow_id = _get_active_workflow_id()
    if active_workflow_id:
        try:
            return repository.load(str(active_workflow_id))
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    documents = repository.list_documents()
    if not documents:
        raise ValueError("No saved workflow documents are available for simulation.")
    if len(documents) == 1:
        return documents[0]

    candidates: list[tuple[float, WorkflowDocument]] = []
    for document in documents:
        path = repository.base_dir / f"{document.workflow_id}.json"
        try:
            candidates.append((path.stat().st_mtime, document))
        except OSError:
            continue
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return documents[-1]


def run_workflow_simulation(
    *,
    specimens_number: int,
    repository: WorkflowRepository | None = None,
    workflow_document: WorkflowDocument | None = None,
    workflow_id: str | None = None,
    random_seed: int | None = None,
    realtime_factor: float | None = None,
    orders: list[object] | None = None,
    scenario: Scenario | None = None,
    on_specimen_completed: Callable[[int, int], None] | None = None,
    on_order_created: Callable[[object], None] | None = None,
    governance_provider: GovernanceProvider | None = None,
    debug_governance: bool = False,
) -> WorkflowSimulationResult:
    """Load a workflow, simulate it, and return the result bundle."""

    repository = repository or WorkflowRepository()
    workflow_document = workflow_document or load_workflow_document_for_simulation(
        repository,
        workflow_id=workflow_id,
    )
    specimens = _build_specimens_from_orders(orders) if orders is not None else None
    resolved_specimens_number = len(specimens) if specimens is not None else specimens_number
    scenario = scenario or workflow_document.workflow.active_scenario
    settings = WorkflowSimulationSettings.from_scenario(
        specimens_number=resolved_specimens_number,
        workflow=workflow_document.workflow,
        scenario=scenario,
        random_seed=random_seed,
        realtime_factor=realtime_factor,
    )
    engine = WorkflowSimulationEngine(
        workflow_document.workflow,
        settings=settings,
        workflow_name=workflow_document.name,
        scenario=scenario,
        specimens=specimens,
        on_specimen_completed=on_specimen_completed,
        on_order_created=on_order_created,
        governance_provider=governance_provider,
        debug_governance=debug_governance,
    )
    return engine.run()


def run_and_print_workflow_simulation(
    *,
    specimens_number: int,
    repository: WorkflowRepository | None = None,
    workflow_id: str | None = None,
    random_seed: int | None = None,
    realtime_factor: float | None = None,
    orders: list[object] | None = None,
    governance_provider: GovernanceProvider | None = None,
    debug_governance: bool = False,
) -> WorkflowSimulationResult:
    """Convenience wrapper for the CLI."""

    repository = repository or WorkflowRepository()
    workflow_document = load_workflow_document_for_simulation(repository, workflow_id=workflow_id)
    specimens = _build_specimens_from_orders(orders) if orders is not None else None
    resolved_specimens_number = len(specimens) if specimens is not None else specimens_number
    scenario = workflow_document.workflow.active_scenario
    settings = WorkflowSimulationSettings.from_scenario(
        specimens_number=resolved_specimens_number,
        workflow=workflow_document.workflow,
        scenario=scenario,
        random_seed=random_seed,
        realtime_factor=realtime_factor,
    )

    total_specimens = len(specimens) if specimens is not None else specimens_number

    def on_specimen_completed(completed: int, total: int) -> None:
        _write_progress_line(completed, total)

    if total_specimens > 0:
        _write_progress_line(0, total_specimens)
    engine = WorkflowSimulationEngine(
        workflow_document.workflow,
        settings=settings,
        workflow_name=workflow_document.name,
        scenario=scenario,
        specimens=specimens,
        on_specimen_completed=on_specimen_completed,
        governance_provider=governance_provider,
        debug_governance=debug_governance,
    )
    result = engine.run()
    if total_specimens > 0:
        _finish_progress_line(len(result.completed_specimens), total_specimens)

    print(f"Workflow: {workflow_document.name}")
    print(f"Workflow ID: {workflow_document.workflow_id}")
    print(f"Specimens: {result.specimens_number}")
    print(f"Makespan: {_format_seconds(result.makespan)}")
    print(result.format_summary_table(workflow_document.workflow, staff_capacity=result.staff_capacity))
    if result.governance_consultations:
        print("Governance consultations:")
        for consultation in result.governance_consultations:
            print(
                "- "
                f"t={_format_seconds(consultation.sim_time)} "
                f"node={consultation.node_id} "
                f"event={consultation.event_type} "
                f"order={consultation.order_id or '-'} "
                f"specimen={consultation.specimen_id or '-'} "
                f"provider={consultation.provider_name} "
                f"action={consultation.decision_action} "
                f"human={consultation.requires_human} "
                f"reason={consultation.reason}"
            )
    return result
