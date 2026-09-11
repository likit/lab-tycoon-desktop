"""SimPy-based workflow simulation for LabTycoon."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
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
from app.system.storage import WorkflowDocument, WorkflowRepository
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

        scenario = scenario or (workflow.scenarios[0] if workflow.scenarios else None)
        parameters = dict(scenario.parameters if scenario is not None else {})

        staff_capacity = parameters.get("staff_capacity")
        if staff_capacity is not None:
            staff_capacity = int(staff_capacity)

        return cls(
            specimens_number=specimens_number,
            random_seed=random_seed,
            realtime_factor=realtime_factor,
            system_variation=float(parameters.get("system_variation", 0.10)),
            human_variation=float(parameters.get("human_variation", 0.10)),
            interarrival_time=float(parameters.get("interarrival_time", 0.0)),
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
        roots = self.start_node_ids(workflow)
        if len(roots) > 1:
            raise ValueError(
                "Workflow has multiple root nodes. Merge the flows into a single chain before simulation."
            )
        if roots:
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

        roots = workflow.graph.root_node_ids()
        active_roots: list[str] = []
        for node_id in roots:
            if workflow.graph.incoming_edges(node_id) or workflow.graph.outgoing_edges(node_id):
                active_roots.append(node_id)
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
        governance_provider: GovernanceProvider | None = None,
        debug_governance: bool = False,
    ) -> None:
        self.workflow = workflow
        self.settings = settings
        self.workflow_name = workflow_name or "Untitled Workflow"
        self.scenario = scenario or (workflow.scenarios[0] if workflow.scenarios else None)
        self.router = router or WorkflowRouter()
        self.on_specimen_completed = on_specimen_completed
        if settings.realtime_factor is not None:
            self.env = simpy.rt.RealtimeEnvironment(factor=float(settings.realtime_factor), strict=False)
        else:
            self.env = simpy.Environment()
        self.rng = random.Random(settings.random_seed)
        self.completed_specimens: list[Specimen] = []
        self.governance_provider = governance_provider or SopAsCodeGovernanceProvider()
        self.governance_consultations: list[GovernanceConsultation] = []
        self.debug_governance = debug_governance
        self._observed_orders: list[object] = []
        self.specimens = (
            specimens
            if specimens is not None
            else [Specimen(specimen_id=f"specimen-{index + 1}") for index in range(settings.specimens_number)]
        )
        self.start_node_id = self.router.start_node_id(self.workflow)
        if self.start_node_id is None:
            self.active_node_ids: list[str] = []
        else:
            self.active_node_ids = self.router.reachable_node_ids(self.workflow, self.start_node_id)
        self.node_resources = {
            node.node_id: simpy.Resource(self.env, capacity=max(1, int(node.widget.capacity)))
            for node in self.workflow.graph.iter_nodes()
            if node.node_id in self.active_node_ids
        }
        self.node_stats = {
            node.node_id: NodeSimulationStats(node_id=node.node_id, node_name=node.widget.name)
            for node in self.workflow.graph.iter_nodes()
            if node.node_id in self.active_node_ids
        }
        self.staff_capacity = self._resolve_staff_capacity()
        self.staff_pool = simpy.Container(
            self.env,
            capacity=self.staff_capacity,
            init=self.staff_capacity,
        )
        self._validate_staff_capacity()

    def _resolve_staff_capacity(self) -> int:
        if self.settings.staff_capacity is not None:
            return max(1, int(self.settings.staff_capacity))

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
            if self._effective_human_processing_capacity(node) <= 0:
                continue

            required_staff = max(1, int(node.widget.required_staff))
            if required_staff > self.staff_capacity:
                raise ValueError(
                    f"Workflow node {node.widget.name!r} requires {required_staff} staff, "
                    f"but the available staff capacity is only {self.staff_capacity}."
                )

    def _effective_human_processing_capacity(self, node: WorkflowNode) -> float:
        return float(node.widget.human_throughput)

    def _sample_service_time(self, capacity: float, variation: float) -> float:
        if capacity <= 0:
            return 0.0
        base_time = 1.0 / capacity
        multiplier = self.rng.uniform(max(0.0, 1.0 - variation), 1.0 + variation)
        return max(0.0, base_time * multiplier)

    def _node_requires_human_attention(self, node: WorkflowNode) -> bool:
        return self._effective_human_processing_capacity(node) > 0

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
                self.completed_specimens.append(specimen)
                if self.on_specimen_completed is not None:
                    self.on_specimen_completed(len(self.completed_specimens), len(self.specimens))
                return True, next_node_override

        return False, next_node_override

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
            yield self.env.timeout(system_processing_time)
            specimen.record_event(
                node.widget.name,
                "system_finished",
                processing_time=system_processing_time,
                time=self.env.now,
            )

            if self._node_requires_human_attention(node):
                required_staff = max(1, int(node.widget.required_staff))
                human_wait_start = self.env.now
                yield self.staff_pool.get(required_staff)
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
                yield self.env.timeout(human_processing_time)
                yield self.staff_pool.put(required_staff)
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
        if node.widget.node_type == "manual analyzer":
            acquired_staff = max(1, int(node.widget.required_staff))
            staff_wait_start = self.env.now
            yield self.staff_pool.get(acquired_staff)
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
                yield self.env.timeout(test_processing_time)
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
                yield self.staff_pool.put(acquired_staff)
                specimen.record_event(
                    node.widget.name,
                    "human_finished",
                    processing_time=total_processing_time,
                    staff_required=acquired_staff,
                    time=self.env.now,
                )

        remaining_tests = [test_code for test_code in pending_tests if test_code not in processable_tests]
        self._set_specimen_pending_tests(specimen, remaining_tests)
        self.node_stats[node.node_id].record(
            wait_time=node_wait_time + staff_wait_time,
            processing_time=total_processing_time,
            staff_busy_time=staff_busy_time,
        )
        return total_processing_time

    def _specimen_process(self, specimen_index: int):
        specimen = self.specimens[specimen_index]
        current_node_id = self.start_node_id
        if current_node_id is None:
            return

        while current_node_id is not None:
            node = self.workflow.graph.get_node(current_node_id)
            if node is None:
                break
            if not node.widget.enabled:
                current_node_id = self.router.next_node_id(self.workflow, node.node_id, specimen)
                continue

            specimen.record_event(node.widget.name, "arrived", time=self.env.now)
            if self._node_uses_duplicate_detection(node):
                self._run_duplicate_detection_adapter(node=node, specimen=specimen)
                current_node_id = self.router.next_node_id(self.workflow, node.node_id, specimen)
                continue
            governance_stopped, next_node_override = self._consult_governance(node=node, specimen=specimen)
            if governance_stopped:
                return
            node_wait_start = self.env.now
            with self.node_resources[node.node_id].request() as node_request:
                yield node_request
                node_wait_time = self.env.now - node_wait_start
                specimen.record_event(node.widget.name, "system_started", wait_time=node_wait_time, time=self.env.now)

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
                    yield self.env.timeout(system_processing_time)
                    specimen.record_event(
                        node.widget.name,
                        "system_finished",
                        processing_time=system_processing_time,
                        time=self.env.now,
                    )

                    if self._node_requires_human_attention(node):
                        required_staff = max(1, int(node.widget.required_staff))
                        human_wait_start = self.env.now
                        yield self.staff_pool.get(required_staff)
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
                        yield self.env.timeout(human_processing_time)
                        yield self.staff_pool.put(required_staff)
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

            current_node_id = next_node_override or self.router.next_node_id(self.workflow, node.node_id, specimen)

        specimen.record_event("workflow", "completed", time=self.env.now)
        self.completed_specimens.append(specimen)
        if self.on_specimen_completed is not None:
            self.on_specimen_completed(len(self.completed_specimens), len(self.specimens))

    def _arrival_process(self):
        for specimen_index in range(len(self.specimens)):
            specimen = self.specimens[specimen_index]
            specimen.metadata["arrival_index"] = specimen_index
            self._observed_orders.append(specimen.payload)
            self.env.process(self._specimen_process(specimen_index))
            if specimen_index + 1 < len(self.specimens) and self.settings.interarrival_time > 0:
                yield self.env.timeout(self.settings.interarrival_time)

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

        self.env.process(self._arrival_process())
        self.env.run()

        return WorkflowSimulationResult(
            workflow_name=self.workflow_name,
            specimens_number=len(self.specimens),
            makespan=self.env.now,
            staff_capacity=self.staff_capacity,
            node_stats=self.node_stats,
            completed_specimens=self.completed_specimens,
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
    on_specimen_completed: Callable[[int, int], None] | None = None,
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
    scenario = workflow_document.workflow.scenarios[0] if workflow_document.workflow.scenarios else None
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
    scenario = workflow_document.workflow.scenarios[0] if workflow_document.workflow.scenarios else None
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
