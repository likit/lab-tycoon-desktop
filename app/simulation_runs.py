"""Persist project simulation runs and their generated orders separately from LIS orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from uuid import uuid4

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.project import LabTycoonProject
from app.system.model import Scenario
from app.system.models import SimulationOrder, SimulationPatient, SimulationRun, engine
from app.system.order_sources import InMemoryOrder
from app.system.simulation import run_workflow_simulation
from app.system.simulation.lab import WorkflowRouter
from app.system.storage import WorkflowDocument


@dataclass(frozen=True, slots=True)
class SimulationRunSummary:
    run_id: str
    scenario_name: str
    generated_orders: int
    completed_specimens: int
    makespan: float


def run_project_scenario(
    project: LabTycoonProject,
    *,
    scenario_id: str,
    orders_number: int,
    random_seed: int | None = None,
    database_engine: Engine | None = None,
) -> SimulationRunSummary:
    """Run one scenario and atomically store its generated orders and provenance."""

    if orders_number <= 0:
        raise ValueError("Number of orders must be positive.")
    scenario: Scenario | None = next(
        (item for item in project.workflow.scenarios if item.scenario_id == scenario_id), None
    )
    if scenario is None:
        raise ValueError("The selected scenario is no longer available.")
    start_id = WorkflowRouter().start_node_id(project.workflow)
    start_node = project.workflow.get_node(start_id) if start_id else None
    if start_node is None or start_node.widget.node_type != "order adapter":
        raise ValueError("Connect an enabled Order Adapter as the workflow source before running this project.")

    generated_orders: list[InMemoryOrder] = []
    document = WorkflowDocument(project.project_id, f"{project.name} Workflow", project.workflow)
    result = run_workflow_simulation(
        specimens_number=orders_number,
        workflow_document=document,
        scenario=scenario,
        random_seed=random_seed,
        on_order_created=generated_orders.append,
    )

    run_id = uuid4().hex
    run = SimulationRun(
        id=run_id,
        project_id=project.project_id,
        scenario_id=scenario.scenario_id,
        scenario_name=scenario.name,
        workflow_name=document.name,
        started_at=datetime.now(timezone.utc),
        random_seed=random_seed,
        requested_orders=orders_number,
    )
    patients: dict[str, SimulationPatient] = {}
    for order in generated_orders:
        patient_id = str(order.customer_id)
        patient = patients.get(patient_id)
        if patient is None:
            patient = SimulationPatient(
                patient_id=patient_id,
                hn=str(order.customer.hn),
                fullname=str(order.customer.fullname),
            )
            patients[patient_id] = patient
            run.patients.append(patient)
        run.orders.append(SimulationOrder(
            order_id=str(order.id),
            patient_id=patient_id,
            patient=patient,
            ordered_at=order.order_datetime,
            priority=str(order.priority),
            test_codes_json=json.dumps([str(item.test.code) for item in order.order_items]),
            metadata_json=json.dumps(order.metadata),
        ))
    with Session(database_engine or engine) as session, session.begin():
        session.add(run)

    return SimulationRunSummary(
        run_id=run_id,
        scenario_name=scenario.name,
        generated_orders=len(generated_orders),
        completed_specimens=len(result.completed_specimens),
        makespan=result.makespan,
    )
