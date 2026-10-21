"""Persistence and provenance tests for project simulation runs."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from app.project import LabTycoonProject
from app.project_workspace import load_customer_rows, load_order_rows, load_result_rows, load_simulation_runs
from app.simulation_runs import run_project_scenario
from app.system.model import Scenario
from app.system.models import (
    Customer, LabOrder, SimulationOrder, SimulationPatient, SimulationRun, ensure_db_schema, initialize_db,
)


class SimulationRunPersistenceTests(unittest.TestCase):
    def _project_with_adapter(self) -> LabTycoonProject:
        project = LabTycoonProject.create("Demo Lab")
        adapter = project.workflow.create_node(
            "Order Intake", kind="order_adapter",
            metadata={"node_type": "order adapter", "adapter_type": "simulation"},
        )
        project.workflow.create_connected_node(adapter.node_id, "Registration", 1.0)
        return project

    def test_same_scenario_can_have_distinct_runs_and_filterable_orders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_engine = create_engine(f"sqlite:///{Path(directory) / 'runs.db'}")
            try:
                with redirect_stdout(io.StringIO()):
                    initialize_db(database_engine)
                with Session(database_engine) as session:
                    initial_lis_patient_count = session.scalar(select(func.count()).select_from(Customer))
                project = self._project_with_adapter()
                resource = project.workflow.create_node(
                    "Patient Resource", kind="resource",
                    metadata={"node_type": "resource", "resource_type": "patient",
                              "adapter_type": "generate_per_run", "enabled": True},
                )
                adapter_id = next(node.node_id for node in project.workflow.graph.iter_nodes()
                                  if node.kind == "order_adapter")
                project.workflow.graph.connect_nodes(resource.node_id, adapter_id)
                scenario_id = project.workflow.scenarios[0].scenario_id

                first = run_project_scenario(
                    project, scenario_id=scenario_id, orders_number=2,
                    random_seed=1, database_engine=database_engine,
                )
                second = run_project_scenario(
                    project, scenario_id=scenario_id, orders_number=1,
                    random_seed=2, database_engine=database_engine,
                )
                alternative = Scenario(name="Night Shift")
                project.workflow.scenarios.append(alternative)
                third = run_project_scenario(
                    project, scenario_id=alternative.scenario_id, orders_number=1,
                    random_seed=3, database_engine=database_engine,
                )

                self.assertNotEqual(first.run_id, second.run_id)
                self.assertEqual((first.generated_orders, second.generated_orders), (2, 1))
                with Session(database_engine) as session:
                    runs = list(session.scalars(select(SimulationRun).order_by(SimulationRun.random_seed)))
                    orders = list(session.scalars(select(SimulationOrder).order_by(SimulationOrder.id)))
                    patients = list(session.scalars(select(SimulationPatient).order_by(SimulationPatient.id)))
                    lis_order_count = session.scalar(select(func.count()).select_from(LabOrder))
                    lis_patient_count = session.scalar(select(func.count()).select_from(Customer))
                self.assertEqual(lis_order_count, 0)
                self.assertEqual(lis_patient_count, initial_lis_patient_count)
                self.assertEqual(len(patients), 4)
                self.assertEqual([order.patient_ref_id for order in orders], [patient.id for patient in patients])
                self.assertEqual(patients[0].fullname, "Simulated Patient 1")
                self.assertEqual(len(runs), 3)
                self.assertEqual([run.scenario_id for run in runs], [scenario_id, scenario_id, alternative.scenario_id])
                self.assertEqual([order.run_id for order in orders], [first.run_id, first.run_id, second.run_id, third.run_id])
                self.assertEqual([order.order_id for order in orders], ["SIM-O0001", "SIM-O0002", "SIM-O0001", "SIM-O0001"])

                with patch("app.project_workspace.engine", database_engine):
                    all_rows = load_order_rows(project_id=project.project_id)
                    sim_rows = load_order_rows(project_id=project.project_id, source="Simulation")
                    first_rows = load_order_rows(
                        project_id=project.project_id, source="Simulation", run_id=first.run_id,
                    )
                    scenario_rows = load_order_rows(
                        project_id=project.project_id, source="Simulation", scenario_id=scenario_id,
                    )
                    lis_rows = load_order_rows(project_id=project.project_id, source="LIS")
                    other_project_rows = load_order_rows(project_id="another-project", source="Simulation")
                    mock_patients = load_customer_rows(
                        project_id=project.project_id, source="Simulation", run_id=first.run_id,
                    )
                    simulation_results = load_result_rows(source="Simulation", run_id=first.run_id)
                    latest_run_id = load_simulation_runs(project.project_id)[0].id

                self.assertEqual(len(sim_rows), 4)
                self.assertEqual(len(first_rows), 2)
                self.assertEqual(len(scenario_rows), 3)
                self.assertEqual(other_project_rows, [])
                self.assertEqual(len(mock_patients), 2)
                self.assertTrue(all(row[6] == "Simulation" and row[8] == first.run_id[:8] for row in mock_patients))
                self.assertEqual(simulation_results, [])
                self.assertEqual(latest_run_id, third.run_id)
                self.assertTrue(all(row[7] == "Simulation" for row in sim_rows))
                self.assertTrue(all(row[9] == first.run_id[:8] for row in first_rows))
                self.assertTrue(all(row[7] == "LIS" for row in lis_rows))
                self.assertGreaterEqual(len(all_rows), len(sim_rows))
            finally:
                database_engine.dispose()

    def test_existing_initialized_database_adds_simulation_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_engine = create_engine(f"sqlite:///{Path(directory) / 'legacy.db'}")
            try:
                with database_engine.begin() as connection:
                    connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
                    connection.execute(text("CREATE TABLE lab_orders (id INTEGER PRIMARY KEY, priority VARCHAR(7))"))

                ensure_db_schema(database_engine)

                inspector = inspect(database_engine)
                self.assertTrue(inspector.has_table("simulation_runs"))
                self.assertTrue(inspector.has_table("simulation_patients"))
                self.assertTrue(inspector.has_table("simulation_orders"))
                self.assertIn("patient_ref_id", {column["name"] for column in inspector.get_columns("simulation_orders")})
            finally:
                database_engine.dispose()

    def test_existing_simulation_orders_gain_patient_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_engine = create_engine(f"sqlite:///{Path(directory) / 'previous.db'}")
            try:
                with database_engine.begin() as connection:
                    connection.execute(text("CREATE TABLE lab_orders (id INTEGER PRIMARY KEY, priority VARCHAR(7))"))
                    connection.execute(text("CREATE TABLE simulation_runs (id VARCHAR(32) PRIMARY KEY)"))
                    connection.execute(text(
                        "CREATE TABLE simulation_orders (id INTEGER PRIMARY KEY, run_id VARCHAR(32), patient_id VARCHAR(64))"
                    ))
                    connection.execute(text("INSERT INTO simulation_runs (id) VALUES ('run1')"))
                    connection.execute(text(
                        "INSERT INTO simulation_orders (id, run_id, patient_id) VALUES "
                        "(1, 'run1', 'SIM-P0001'), (2, 'run1', 'SIM-P0001')"
                    ))

                ensure_db_schema(database_engine)
                ensure_db_schema(database_engine)

                with database_engine.connect() as connection:
                    patients = connection.execute(text("SELECT id, patient_id FROM simulation_patients")).all()
                    orders = connection.execute(text("SELECT patient_ref_id FROM simulation_orders ORDER BY id")).all()
                self.assertEqual(len(patients), 1)
                self.assertEqual(patients[0].patient_id, "SIM-P0001")
                self.assertEqual([order.patient_ref_id for order in orders], [patients[0].id, patients[0].id])
            finally:
                database_engine.dispose()
