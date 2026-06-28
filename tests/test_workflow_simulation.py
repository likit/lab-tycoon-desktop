"""Tests for the SimPy workflow simulation engine."""

from __future__ import annotations

from datetime import datetime
import tempfile
import unittest
from unittest.mock import patch

import simpy

from app.system.model import Workflow
from app.system.order_sources import InMemoryCustomer, InMemoryOrder, InMemoryOrderItem, InMemoryTest
from app.system.simulation import (
    WorkflowSimulationEngine,
    WorkflowSimulationSettings,
    load_workflow_document_for_simulation,
    run_workflow_simulation,
)
from app.system.storage import WorkflowDocument, WorkflowRepository


class WorkflowSimulationEngineTests(unittest.TestCase):
    def test_simulation_traverses_nodes_and_collects_metrics(self) -> None:
        workflow = Workflow()
        intake = workflow.create_node("Intake", 1.0, metadata={"capacity": 2})
        review = workflow.create_node(
            "Review",
            1.0,
            metadata={
                "node_type": "manual analyzer",
                "human_throughput": 2.0,
                "required_staff": 1,
            },
        )
        workflow.graph.connect_nodes(intake.node_id, review.node_id)
        workflow.create_connected_node(review.node_id, "Release", 1.0)

        settings = WorkflowSimulationSettings(
            specimens_number=2,
            random_seed=7,
            system_variation=0.0,
            human_variation=0.0,
            staff_capacity=1,
        )
        engine = WorkflowSimulationEngine(
            workflow,
            settings=settings,
            workflow_name="Test Workflow",
        )
        result = engine.run()

        self.assertEqual(result.workflow_name, "Test Workflow")
        self.assertEqual(len(result.completed_specimens), 2)
        self.assertGreater(result.makespan, 0.0)

        intake_stats = result.node_stats[intake.node_id]
        review_stats = result.node_stats[review.node_id]
        self.assertEqual(intake_stats.specimens_processed, 2)
        self.assertEqual(review_stats.specimens_processed, 2)
        self.assertGreater(intake_stats.average_wait_time, 0.0)
        self.assertGreater(review_stats.average_wait_time, 0.0)
        self.assertGreater(review_stats.total_staff_busy_time, 0.0)
        self.assertIn("staff", review_stats.utilization(result.makespan, staff_capacity=result.staff_capacity))
        self.assertEqual(engine.node_resources[intake.node_id].capacity, 2)

    def test_loaded_orders_are_simulated_as_specimens(self) -> None:
        workflow = Workflow()
        intake = workflow.create_node("Intake", 1.0)
        analyzer = workflow.create_node(
            "Analyzer",
            0.0,
            metadata={
                "node_type": "manual analyzer",
                "human_throughput": 2.0,
                "required_staff": 1,
                "analyzer_tests": ["CBC"],
            },
        )
        workflow.graph.connect_nodes(intake.node_id, analyzer.node_id)
        workflow.create_connected_node(analyzer.node_id, "Release", 1.0)

        order = InMemoryOrder(
            id="ORD-1",
            customer_id="P001",
            order_datetime=datetime(2026, 5, 2, 10, 0, 0),
            order_items=[
                InMemoryOrderItem(InMemoryTest("CBC")),
                InMemoryOrderItem(InMemoryTest("GLU")),
            ],
            priority="routine",
            customer=InMemoryCustomer(id="P001", hn="P001", fullname="Uploaded Patient P001"),
        )

        result = run_workflow_simulation(
            specimens_number=1,
            workflow_document=WorkflowDocument.create("Workflow", workflow=workflow),
            orders=[order],
            random_seed=7,
        )

        self.assertEqual(result.specimens_number, 1)
        self.assertEqual(len(result.completed_specimens), 1)
        self.assertEqual(result.completed_specimens[0].specimen_id, "ORD-1")
        self.assertEqual(result.completed_specimens[0].metadata["pending_tests"], ["GLU"])
        analyzer_stats = result.node_stats[analyzer.node_id]
        self.assertEqual(analyzer_stats.specimens_processed, 1)
        self.assertGreater(analyzer_stats.total_processing_time, 0.0)

    def test_realtime_factor_uses_realtime_environment(self) -> None:
        workflow = Workflow()
        workflow.create_node("Intake", 1.0)

        settings = WorkflowSimulationSettings(
            specimens_number=1,
            random_seed=7,
            realtime_factor=0.1,
        )
        engine = WorkflowSimulationEngine(
            workflow,
            settings=settings,
            workflow_name="Test Workflow",
        )

        self.assertIsInstance(engine.env, simpy.rt.RealtimeEnvironment)

    def test_progress_callback_reports_completed_specimens(self) -> None:
        workflow = Workflow()
        intake = workflow.create_node("Intake", 1.0)
        workflow.create_connected_node(intake.node_id, "Release", 1.0)

        updates: list[tuple[int, int]] = []
        settings = WorkflowSimulationSettings(specimens_number=2, random_seed=7, system_variation=0.0)
        engine = WorkflowSimulationEngine(
            workflow,
            settings=settings,
            workflow_name="Test Workflow",
            on_specimen_completed=lambda completed, total: updates.append((completed, total)),
        )

        result = engine.run()

        self.assertEqual(result.specimens_number, 2)
        self.assertEqual(updates, [(1, 2), (2, 2)])

    def test_multiple_roots_raise_error_before_simulation(self) -> None:
        workflow = Workflow()
        left_root = workflow.create_node("Left Root", 1.0)
        workflow.create_connected_node(left_root.node_id, "Left End", 1.0)
        right_root = workflow.create_node("Right Root", 1.0)
        workflow.create_connected_node(right_root.node_id, "Right End", 1.0)

        settings = WorkflowSimulationSettings(specimens_number=1, random_seed=7)

        with self.assertRaisesRegex(ValueError, "multiple root nodes"):
            WorkflowSimulationEngine(
                workflow,
                settings=settings,
                workflow_name="Broken Workflow",
            )

    def test_orphan_nodes_are_ignored_in_simulation_and_summary(self) -> None:
        workflow = Workflow()
        intake = workflow.create_node("Intake", 1.0)
        review = workflow.create_connected_node(intake.node_id, "Review", 1.0)
        workflow.create_node("Orphan", 1.0)

        settings = WorkflowSimulationSettings(
            specimens_number=1,
            random_seed=7,
            system_variation=0.0,
            human_variation=0.0,
        )
        engine = WorkflowSimulationEngine(
            workflow,
            settings=settings,
            workflow_name="Test Workflow",
        )
        result = engine.run()

        self.assertNotIn("Orphan", {stats.node_name for stats in result.node_stats.values()})
        summary = result.format_summary_table(workflow, staff_capacity=result.staff_capacity)
        self.assertIn("Intake", summary)
        self.assertIn("Review", summary)
        self.assertNotIn("Orphan", summary)


class WorkflowSelectionTests(unittest.TestCase):
    def test_active_workflow_id_is_used_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repository = WorkflowRepository(tmpdir)
            first = WorkflowDocument.create("First")
            second = WorkflowDocument.create("Second")
            repository.save(first)
            repository.save(second)

            with patch("app.system.simulation.lab._get_active_workflow_id", return_value=second.workflow_id):
                loaded = load_workflow_document_for_simulation(repository)

            self.assertEqual(loaded.workflow_id, second.workflow_id)

    def test_explicit_workflow_id_overrides_active_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repository = WorkflowRepository(tmpdir)
            first = WorkflowDocument.create("First")
            second = WorkflowDocument.create("Second")
            repository.save(first)
            repository.save(second)

            with patch("app.system.simulation.lab._get_active_workflow_id", return_value=first.workflow_id):
                loaded = load_workflow_document_for_simulation(repository, workflow_id=second.workflow_id)

            self.assertEqual(loaded.workflow_id, second.workflow_id)


if __name__ == "__main__":
    unittest.main()
