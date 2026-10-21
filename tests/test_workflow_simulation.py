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
    def test_shared_pools_use_both_centrifuges_and_analyzers_once_per_specimen(self):
        workflow = Workflow()
        intake = workflow.create_node("Processing", 0)
        centrifuges = [workflow.create_node(f"Centrifuge {i}", 1, kind="equipment",
            metadata={"equipment_id": f"CENT-{i}"}) for i in range(2)]
        analyzers = [workflow.create_node(f"Analyzer {i}", 1, kind="equipment",
            metadata={"equipment_id": f"CHEM-{i}", "node_type": "automated analyzer"}) for i in range(2)]
        reporting = workflow.create_node("Reporting", 0)
        for centrifuge in centrifuges:
            workflow.graph.connect_nodes(intake.node_id, centrifuge.node_id)
            for analyzer in analyzers:
                workflow.graph.connect_nodes(centrifuge.node_id, analyzer.node_id)
        for analyzer in analyzers:
            workflow.graph.connect_nodes(analyzer.node_id, reporting.node_id)
        engine = WorkflowSimulationEngine(workflow, settings=WorkflowSimulationSettings(
            specimens_number=4, system_variation=0, human_variation=0))
        result = engine.run()
        self.assertEqual(len(result.completed_specimens), 4)
        self.assertEqual(result.makespan, 3)
        for node in centrifuges + analyzers:
            self.assertEqual(result.node_stats[node.node_id].specimens_processed, 2)
        self.assertEqual(result.node_stats[reporting.node_id].specimens_processed, 4)

    def test_equipment_pool_prefers_faster_machine_but_uses_idle_alternative(self):
        workflow = Workflow()
        intake = workflow.create_node("Intake", 0)
        fast = workflow.create_connected_node(intake.node_id, "Fast", 2, kind="equipment")
        slow = workflow.create_connected_node(intake.node_id, "Slow", 1, kind="equipment")
        engine = WorkflowSimulationEngine(workflow, settings=WorkflowSimulationSettings(
            specimens_number=4, system_variation=0))
        result = engine.run()
        self.assertGreater(result.node_stats[fast.node_id].specimens_processed, 0)
        self.assertGreater(result.node_stats[slow.node_id].specimens_processed, 0)
        self.assertEqual(len(result.completed_specimens), 4)

    def test_task_branches_keep_first_connection_routing(self):
        workflow = Workflow()
        intake = workflow.create_node("Intake", 0)
        first = workflow.create_connected_node(intake.node_id, "First", 1)
        second = workflow.create_connected_node(intake.node_id, "Second", 1)
        result = WorkflowSimulationEngine(workflow, settings=WorkflowSimulationSettings(
            specimens_number=2, system_variation=0)).run()
        self.assertEqual(result.node_stats[first.node_id].specimens_processed, 2)
        self.assertEqual(result.node_stats[second.node_id].specimens_processed, 0)

    def test_pool_excludes_disabled_equipment(self):
        workflow = Workflow()
        intake = workflow.create_node("Intake", 0)
        disabled = workflow.create_connected_node(intake.node_id, "Disabled", 10,
            kind="equipment", metadata={"enabled": False})
        active = workflow.create_connected_node(intake.node_id, "Active", 1, kind="equipment")
        result = WorkflowSimulationEngine(workflow, settings=WorkflowSimulationSettings(
            specimens_number=2, system_variation=0)).run()
        self.assertNotIn(disabled.node_id, result.node_stats)
        self.assertEqual(result.node_stats[active.node_id].specimens_processed, 2)

    def test_analyzers_with_different_test_menus_are_not_equivalent_pool(self):
        workflow = Workflow()
        intake = workflow.create_node("Intake", 0)
        first = workflow.create_connected_node(intake.node_id, "CBC", 1, kind="equipment",
            metadata={"node_type": "automated analyzer", "analyzer_tests": ["CBC"]})
        workflow.create_connected_node(intake.node_id, "Chemistry", 100, kind="equipment",
            metadata={"node_type": "automated analyzer", "analyzer_tests": ["GLU"]})
        engine = WorkflowSimulationEngine(workflow, settings=WorkflowSimulationSettings(specimens_number=1))
        self.assertEqual(engine._next_node_id(intake.node_id, engine.specimens[0]), first.node_id)

    def test_explicit_router_retains_control_over_equipment_selection(self):
        from app.system.simulation.lab import WorkflowRouter

        workflow = Workflow()
        intake = workflow.create_node("Intake", 0)
        first = workflow.create_connected_node(intake.node_id, "First", 1, kind="equipment")
        workflow.create_connected_node(intake.node_id, "Faster", 100, kind="equipment")
        engine = WorkflowSimulationEngine(workflow, router=WorkflowRouter(),
            settings=WorkflowSimulationSettings(specimens_number=2, system_variation=0))
        result = engine.run()
        self.assertEqual(result.node_stats[first.node_id].specimens_processed, 2)

    def test_nodes_for_same_equipment_share_resource_and_queue(self):
        workflow = Workflow()
        first = workflow.create_node("First operation", 1, kind="equipment",
            metadata={"equipment_id": "CHEM-001", "capacity": 1})
        second = workflow.create_connected_node(first.node_id, "Second operation", 1, kind="equipment",
            metadata={"equipment_id": "CHEM-001", "capacity": 1})
        settings = WorkflowSimulationSettings(specimens_number=2, system_variation=0, human_variation=0)
        engine = WorkflowSimulationEngine(workflow, settings=settings)
        self.assertIs(engine.node_resources[first.node_id], engine.node_resources[second.node_id])
        self.assertIs(engine.equipment_resources['CHEM-001'], engine.node_resources[first.node_id])
        result = engine.run()
        self.assertEqual(result.makespan, 4)
        self.assertEqual(len(result.completed_specimens), 2)
        self.assertGreater(result.node_stats[second.node_id].total_wait_time, 0)
        other_engine = WorkflowSimulationEngine(workflow, settings=settings)
        self.assertIsNot(other_engine.equipment_resources['CHEM-001'], engine.equipment_resources['CHEM-001'])

    def test_distinct_equipment_and_process_nodes_remain_independent(self):
        workflow = Workflow()
        first = workflow.create_node("First", 1, kind="equipment", metadata={"equipment_id": "CHEM-001"})
        second = workflow.create_connected_node(first.node_id, "Second", 1, kind="equipment", metadata={"equipment_id": "CHEM-002"})
        process = workflow.create_connected_node(second.node_id, "Reporting", 1, metadata={"attend_equipment_id": "CHEM-001"})
        engine = WorkflowSimulationEngine(workflow, settings=WorkflowSimulationSettings(specimens_number=2, system_variation=0))
        self.assertIsNot(engine.node_resources[first.node_id], engine.node_resources[second.node_id])
        self.assertIsNot(engine.node_resources[first.node_id], engine.node_resources[process.node_id])
        self.assertEqual(engine.run().makespan, 4)

    def test_shared_equipment_rejects_conflicting_capacities(self):
        workflow = Workflow()
        first = workflow.create_node("First", 1, kind="equipment", metadata={"equipment_id": "CHEM-001", "capacity": 1})
        workflow.create_connected_node(first.node_id, "Second", 1, kind="equipment", metadata={"equipment_id": "CHEM-001", "capacity": 2})
        with self.assertRaisesRegex(ValueError, "CHEM-001.*conflicting capacities"):
            WorkflowSimulationEngine(workflow, settings=WorkflowSimulationSettings(specimens_number=1))

    def test_reporting_station_adds_no_processing_time(self):
        workflow = Workflow()
        analyzer = workflow.create_node("Analyzer", 10)
        reporting = workflow.create_connected_node(analyzer.node_id, "Reporting", 20)
        validating = workflow.create_connected_node(reporting.node_id, "Validating", 30)
        document = WorkflowDocument.create("Workflow", workflow=workflow)
        baseline = run_workflow_simulation(specimens_number=2, workflow_document=document, random_seed=7)
        station = workflow.create_connected_node(validating.node_id, "Reporting Station", 999,
            kind="equipment", metadata={"terminal_output": True, "required_staff": 1, "human_throughput": 1})
        result = run_workflow_simulation(specimens_number=2, workflow_document=document, random_seed=7)
        self.assertEqual(result.makespan, baseline.makespan)
        self.assertEqual(len(result.completed_specimens), 2)
        self.assertEqual(result.node_stats[station.node_id].specimens_processed, 0)
        self.assertTrue(any(event["event"] == "report_received" for event in result.completed_specimens[0].history))

    def test_simulation_order_adapter_forwards_order_to_downstream_node(self) -> None:
        workflow = Workflow()
        adapter = workflow.create_node(
            "Order Intake", kind="order_adapter",
            metadata={"node_type": "order adapter", "adapter_type": "simulation"},
        )
        registration = workflow.create_connected_node(adapter.node_id, "Registration", 1.0)

        result = run_workflow_simulation(
            specimens_number=2,
            workflow_document=WorkflowDocument.create("Workflow", workflow=workflow),
            random_seed=7,
        )

        self.assertEqual(result.specimens_number, 2)
        self.assertEqual(len(result.completed_specimens), 2)
        self.assertEqual(result.node_stats[registration.node_id].specimens_processed, 2)
        self.assertEqual(result.node_stats[adapter.node_id].specimens_processed, 0)
        first = result.completed_specimens[0]
        self.assertIsInstance(first.payload, InMemoryOrder)
        self.assertEqual(first.metadata["order_source"], "simulation")
        self.assertEqual(first.specimen_id, "SIM-O0001")
        self.assertEqual(first.history[0]["event"], "order_created")
        self.assertTrue(any(event["widget"] == "Registration" for event in first.history))

    def test_order_adapter_with_incoming_edge_is_invalid(self) -> None:
        workflow = Workflow()
        source = workflow.create_node("Source", 1.0)
        adapter = workflow.create_connected_node(
            source.node_id, "Order Intake", kind="order_adapter",
            metadata={"node_type": "order adapter", "adapter_type": "simulation"},
        )
        workflow.create_connected_node(adapter.node_id, "Registration", 1.0)

        with self.assertRaisesRegex(ValueError, "incoming order-flow connections"):
            run_workflow_simulation(
                specimens_number=1,
                workflow_document=WorkflowDocument.create("Workflow", workflow=workflow),
            )

    def test_disabled_order_adapter_does_not_generate_orders(self) -> None:
        workflow = Workflow()
        adapter = workflow.create_node(
            "Order Intake", kind="order_adapter",
            metadata={"node_type": "order adapter", "adapter_type": "simulation", "enabled": False},
        )
        workflow.create_connected_node(adapter.node_id, "Registration", 1.0)

        result = run_workflow_simulation(
            specimens_number=2,
            workflow_document=WorkflowDocument.create("Workflow", workflow=workflow),
        )

        self.assertEqual(result.completed_specimens, [])

    def test_legacy_workflow_without_adapter_uses_existing_synthetic_specimen(self) -> None:
        workflow = Workflow()
        intake = workflow.create_node("Intake", 1.0)
        workflow.create_connected_node(intake.node_id, "Release", 1.0)

        result = run_workflow_simulation(
            specimens_number=1,
            workflow_document=WorkflowDocument.create("Workflow", workflow=workflow),
            random_seed=7,
        )

        self.assertEqual(result.completed_specimens[0].specimen_id, "specimen-1")
        self.assertIsNone(result.completed_specimens[0].payload)

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
        self.assertEqual(intake_stats.average_wait_time, 0.0)
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
                "analyzer_tests": ["CBC", "GLU"],
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
        self.assertEqual(result.completed_specimens[0].metadata["pending_tests"], [])
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

    def test_disabled_analyzer_is_bypassed_in_favor_of_active_analyzer(self) -> None:
        workflow = Workflow()
        intake = workflow.create_node("Intake", 1.0)
        skipped_analyzer = workflow.create_node(
            "Analyzer A",
            1.0,
            metadata={
                "node_type": "automated analyzer",
                "analyzer_tests": ["CBC"],
                "enabled": False,
            },
        )
        active_analyzer = workflow.create_node(
            "Analyzer B",
            1.0,
            metadata={
                "node_type": "automated analyzer",
                "analyzer_tests": ["CBC"],
            },
        )
        workflow.graph.connect_nodes(intake.node_id, skipped_analyzer.node_id)
        workflow.graph.connect_nodes(skipped_analyzer.node_id, active_analyzer.node_id)
        workflow.create_connected_node(active_analyzer.node_id, "Release", 1.0)

        order = InMemoryOrder(
            id="ORD-2",
            customer_id="P002",
            order_datetime=datetime(2026, 5, 2, 11, 0, 0),
            order_items=[InMemoryOrderItem(InMemoryTest("CBC"))],
            priority="routine",
            customer=InMemoryCustomer(id="P002", hn="P002", fullname="Uploaded Patient P002"),
        )

        result = run_workflow_simulation(
            specimens_number=1,
            workflow_document=WorkflowDocument.create("Workflow", workflow=workflow),
            orders=[order],
            random_seed=7,
        )

        self.assertEqual(len(result.completed_specimens), 1)
        self.assertNotIn(skipped_analyzer.node_id, result.node_stats)
        self.assertEqual(result.node_stats[active_analyzer.node_id].specimens_processed, 1)
        self.assertEqual(result.completed_specimens[0].metadata["pending_tests"], [])


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
