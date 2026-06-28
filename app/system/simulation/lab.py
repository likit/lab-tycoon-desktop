"""SimPy-based workflow simulation for LabTycoon."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import random

import simpy

from app.system.model import Scenario, Specimen, Workflow, WorkflowNode
from app.system.storage import WorkflowDocument, WorkflowRepository


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


@dataclass(slots=True)
class WorkflowSimulationSettings:
    """Runtime settings for a workflow simulation."""

    specimens_number: int
    random_seed: int | None = None
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

    def summary_rows(self, workflow: Workflow, staff_capacity: int | None = None) -> list[list[str]]:
        """Build rows for the summary table."""

        rows: list[list[str]] = []
        for node in workflow.graph.iter_nodes():
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


class WorkflowRouter:
    """Route specimens through a workflow graph."""

    def start_node_id(self, workflow: Workflow) -> str | None:
        roots = workflow.graph.root_node_ids()
        if roots:
            return roots[0]
        nodes = workflow.graph.iter_nodes()
        return nodes[0].node_id if nodes else None

    def next_node_id(self, workflow: Workflow, current_node_id: str, specimen: Specimen) -> str | None:
        outgoing = workflow.graph.outgoing_edges(current_node_id)
        if not outgoing:
            return None
        return outgoing[0].target


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
    ) -> None:
        self.workflow = workflow
        self.settings = settings
        self.workflow_name = workflow_name or "Untitled Workflow"
        self.scenario = scenario or (workflow.scenarios[0] if workflow.scenarios else None)
        self.router = router or WorkflowRouter()
        self.env = simpy.Environment()
        self.rng = random.Random(settings.random_seed)
        self.completed_specimens: list[Specimen] = []
        self.node_resources = {
            node.node_id: simpy.Resource(self.env, capacity=1)
            for node in self.workflow.graph.iter_nodes()
        }
        self.node_stats = {
            node.node_id: NodeSimulationStats(node_id=node.node_id, node_name=node.widget.name)
            for node in self.workflow.graph.iter_nodes()
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

        required_staff = [
            int(node.widget.required_staff)
            for node in self.workflow.graph.iter_nodes()
            if int(node.widget.required_staff) > 0 or float(node.widget.human_throughput) > 0
        ]
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

    def _specimen_process(self, specimen_index: int):
        specimen = Specimen(specimen_id=f"specimen-{specimen_index + 1}")
        current_node_id = self.router.start_node_id(self.workflow)
        if current_node_id is None:
            return

        while current_node_id is not None:
            node = self.workflow.graph.get_node(current_node_id)
            if node is None:
                break

            specimen.record_event(node.widget.name, "arrived", time=self.env.now)
            node_wait_start = self.env.now
            with self.node_resources[node.node_id].request() as node_request:
                yield node_request
                node_wait_time = self.env.now - node_wait_start
                specimen.record_event(node.widget.name, "system_started", wait_time=node_wait_time, time=self.env.now)

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

                human_wait_time = 0.0
                human_processing_time = 0.0
                staff_busy_time = 0.0

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

            current_node_id = self.router.next_node_id(self.workflow, node.node_id, specimen)

        specimen.record_event("workflow", "completed", time=self.env.now)
        self.completed_specimens.append(specimen)

    def _arrival_process(self):
        for specimen_index in range(self.settings.specimens_number):
            self.env.process(self._specimen_process(specimen_index))
            if specimen_index + 1 < self.settings.specimens_number and self.settings.interarrival_time > 0:
                yield self.env.timeout(self.settings.interarrival_time)

    def run(self) -> WorkflowSimulationResult:
        """Execute the simulation and return a summary."""

        if not self.workflow.graph.nodes:
            return WorkflowSimulationResult(
                workflow_name=self.workflow_name,
                specimens_number=self.settings.specimens_number,
                makespan=0.0,
                staff_capacity=self.staff_capacity,
            )

        self.env.process(self._arrival_process())
        self.env.run()

        return WorkflowSimulationResult(
            workflow_name=self.workflow_name,
            specimens_number=self.settings.specimens_number,
            makespan=self.env.now,
            staff_capacity=self.staff_capacity,
            node_stats=self.node_stats,
            completed_specimens=self.completed_specimens,
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
) -> WorkflowSimulationResult:
    """Load a workflow, simulate it, and return the result bundle."""

    repository = repository or WorkflowRepository()
    workflow_document = workflow_document or load_workflow_document_for_simulation(
        repository,
        workflow_id=workflow_id,
    )
    scenario = workflow_document.workflow.scenarios[0] if workflow_document.workflow.scenarios else None
    settings = WorkflowSimulationSettings.from_scenario(
        specimens_number=specimens_number,
        workflow=workflow_document.workflow,
        scenario=scenario,
        random_seed=random_seed,
    )
    engine = WorkflowSimulationEngine(
        workflow_document.workflow,
        settings=settings,
        workflow_name=workflow_document.name,
        scenario=scenario,
    )
    return engine.run()


def run_and_print_workflow_simulation(
    *,
    specimens_number: int,
    repository: WorkflowRepository | None = None,
    workflow_id: str | None = None,
    random_seed: int | None = None,
) -> WorkflowSimulationResult:
    """Convenience wrapper for the CLI."""

    repository = repository or WorkflowRepository()
    workflow_document = load_workflow_document_for_simulation(repository, workflow_id=workflow_id)
    scenario = workflow_document.workflow.scenarios[0] if workflow_document.workflow.scenarios else None
    settings = WorkflowSimulationSettings.from_scenario(
        specimens_number=specimens_number,
        workflow=workflow_document.workflow,
        scenario=scenario,
        random_seed=random_seed,
    )
    engine = WorkflowSimulationEngine(
        workflow_document.workflow,
        settings=settings,
        workflow_name=workflow_document.name,
        scenario=scenario,
    )
    result = engine.run()

    print(f"Workflow: {workflow_document.name}")
    print(f"Workflow ID: {workflow_document.workflow_id}")
    print(f"Specimens: {specimens_number}")
    print(f"Makespan: {_format_seconds(result.makespan)}")
    print(result.format_summary_table(workflow_document.workflow, staff_capacity=result.staff_capacity))
    return result
