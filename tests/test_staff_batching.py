"""Arrival clustering and staff preparation batches retain per-order identity."""
import unittest

from app.system.model import Workflow
from app.system.simulation import WorkflowSimulationEngine, WorkflowSimulationSettings
from app.system.staff_batching import staff_batch_settings
from app.system.storage import WorkflowDocument
from app.system.workflow_editor import WorkflowEditorController


class StaffBatchingTests(unittest.TestCase):
    def build(self, *, count=3, size=5, wait=1, interval=0, capacity=1, staff=1, human=60):
        workflow = Workflow()
        adapter = workflow.create_node("Orders", kind="order_adapter",
            metadata={"node_type": "order adapter", "adapter_type": "simulation"})
        preparation = workflow.create_connected_node(adapter.node_id, "Specimens Processing", 0,
            metadata={"node_type": "specimens processing", "human_throughput": human,
                      "required_staff": 1, "capacity": capacity,
                      "batch_size": size, "batch_wait_minutes": wait})
        workflow.create_connected_node(preparation.node_id, "Downstream", 0)
        events = []
        engine = WorkflowSimulationEngine(workflow, on_activity=events.append,
            settings=WorkflowSimulationSettings(specimens_number=count,
                system_variation=0, human_variation=0, staff_capacity=staff,
                interarrival_time=interval / 60))
        return workflow, preparation, events, engine

    def history(self, engine, name):
        return [event for specimen in engine.specimens for event in specimen.history if event["event"] == name]

    def test_default_generated_orders_arrive_at_identical_simulated_time(self):
        _, _, _, engine = self.build()
        engine.run()
        arrivals = self.history(engine, "order_created")
        self.assertEqual(len(arrivals), 3)
        self.assertEqual({event["details"]["time"] for event in arrivals}, {0})
        self.assertEqual(len({specimen.specimen_id for specimen in engine.specimens}), 3)

    def test_close_arrivals_collect_until_size_and_acquire_staff_once(self):
        _, node, activities, engine = self.build(size=3, interval=.2)
        result = engine.run()
        formed = self.history(engine, "batch_formed")
        self.assertEqual(len({event["details"]["batch_id"] for event in formed}), 1)
        self.assertTrue(all(event["details"]["batch_size"] == 3 for event in formed))
        self.assertAlmostEqual(formed[0]["details"]["time"] * 60, .4)
        self.assertEqual(sum(event["event"] == "staff_acquired" for event in activities), 1)
        self.assertAlmostEqual(result.node_stats[node.node_id].total_staff_busy_time * 60, 3)
        self.assertAlmostEqual(result.makespan * 60, 3.4)
        self.assertEqual(len(result.completed_specimens), 3)
        downstream = [event for event in activities if event["event"] == "arrived" and event["node_id"] != node.node_id]
        self.assertEqual(len({event["time"] for event in downstream}), 1)
        self.assertEqual(engine.staff_pool.level, 1)

    def test_partial_batch_flushes_at_oldest_arrival_deadline(self):
        _, _, activities, engine = self.build(size=5, wait=1, count=2, interval=.2)
        result = engine.run()
        acquired = [event for event in activities if event["event"] == "staff_acquired"]
        self.assertEqual(len(acquired), 1)
        self.assertAlmostEqual(acquired[0]["time"] * 60, 1)
        self.assertEqual(acquired[0]["batch_size"], 2)
        self.assertAlmostEqual(result.makespan * 60, 3)

    def test_distant_arrivals_do_not_join_same_batch(self):
        _, _, activities, engine = self.build(count=2, interval=3)
        engine.run()
        acquired = [event for event in activities if event["event"] == "staff_acquired"]
        self.assertEqual([event["batch_size"] for event in acquired], [1, 1])
        self.assertNotEqual(acquired[0]["batch_id"], acquired[1]["batch_id"])

    def test_full_batches_and_final_partial_conserve_every_order(self):
        _, node, activities, engine = self.build(count=7, size=3)
        result = engine.run()
        acquired = [event for event in activities if event["event"] == "staff_acquired"]
        self.assertEqual([event["batch_size"] for event in acquired], [3, 3, 1])
        self.assertEqual(len(result.completed_specimens), 7)
        self.assertEqual(result.node_stats[node.node_id].specimens_processed, 7)
        self.assertEqual(sum(event["event"] == "staff_released" for event in activities), 3)
        self.assertTrue(all(sum(event["event"] == "completed" for event in specimen.history) == 1
                            for specimen in result.completed_specimens))

    def test_capacity_and_available_staff_limit_parallel_batches(self):
        for staff, expected_minutes in ((1, 4), (2, 2)):
            with self.subTest(staff=staff):
                _, _, activities, engine = self.build(count=4, size=2, capacity=2, staff=staff)
                result = engine.run()
                self.assertAlmostEqual(result.makespan * 60, expected_minutes)
                active = 0
                for event in activities:
                    if event["event"] == "staff_acquired":
                        active += event["staff_count"]
                    elif event["event"] == "staff_released":
                        active -= event["staff_count"]
                    self.assertLessEqual(active, staff)
                    self.assertGreaterEqual(active, 0)
                self.assertEqual(active, 0)

    def test_batch_size_one_retains_individual_processing(self):
        _, _, activities, engine = self.build(size=1, wait=10)
        result = engine.run()
        self.assertEqual(self.history(engine, "batch_formed"), [])
        self.assertEqual(sum(event["event"] == "staff_acquired" for event in activities), 3)
        self.assertAlmostEqual(result.makespan * 60, 3)

    def test_collection_timer_does_not_extend_completed_run(self):
        _, _, _, engine = self.build(count=2, size=2, wait=10, interval=.1, human=60000)
        result = engine.run()
        self.assertAlmostEqual(result.makespan * 60, .102)

    def test_batch_settings_round_trip_and_validate_before_mutation(self):
        workflow, node, _, _ = self.build()
        controller = WorkflowEditorController(on_change=lambda: None,
            current_document=WorkflowDocument.create("Batch workflow", workflow=workflow))
        form = dict(node_type="specimens processing", name=node.widget.name, description="",
                    throughput=0, human_throughput=60, capacity=1, required_staff=1)
        controller.update_node_from_form(node.node_id, batch_size=8, batch_wait_minutes=2.5, **form)
        restored = Workflow.from_dict(workflow.to_dict()).get_node(node.node_id)
        self.assertEqual(staff_batch_settings(restored), (8, 2.5))
        for size, wait in ((0, 1), (1.5, 1), (2, -1), (2, float("nan"))):
            with self.subTest(size=size, wait=wait):
                with self.assertRaises(ValueError):
                    controller.update_node_from_form(node.node_id, batch_size=size, batch_wait_minutes=wait, **form)
        self.assertEqual(staff_batch_settings(node), (8, 2.5))

    def test_batches_flow_through_shared_equipment_and_report_complete_orders(self):
        workflow, prep, _, engine = self.build(count=6, size=3)
        downstream = next(node for node in workflow.graph.iter_nodes() if node.widget.name == "Downstream")
        centrifuges = [workflow.create_connected_node(downstream.node_id, f"Centrifuge {i}", 60,
            kind="equipment", metadata={"equipment_id": f"CENT-{i}"}) for i in range(2)]
        analyzers = [workflow.create_node(code, 60, kind="equipment",
            metadata={"node_type": "automated analyzer", "equipment_id": code,
                      "analyzer_tests": [code]}) for code in ("CBC", "GLU")]
        report = workflow.create_node("Reporting", 0)
        for centrifuge in centrifuges:
            for analyzer in analyzers:
                workflow.graph.connect_nodes(centrifuge.node_id, analyzer.node_id)
        for analyzer in analyzers:
            workflow.graph.connect_nodes(analyzer.node_id, report.node_id)
        engine = WorkflowSimulationEngine(workflow, settings=engine.settings)
        result = engine.run()
        self.assertEqual(len(result.completed_specimens), 6)
        self.assertEqual(result.node_stats[report.node_id].specimens_processed, 6)
        self.assertTrue(all(result.node_stats[node.node_id].specimens_processed > 0 for node in centrifuges + analyzers))
        for specimen in result.completed_specimens:
            self.assertCountEqual(specimen.metadata["completed_tests"], ["CBC", "GLU"])
            self.assertEqual(specimen.metadata["pending_tests"], [])
            self.assertEqual(sum(event["event"] == "completed" for event in specimen.history), 1)

    def test_live_session_finishes_with_batch_workers_waiting_for_future_arrivals(self):
        import time
        from app.system.live_simulation import LiveSimulationSession

        workflow, _, _, _ = self.build(count=2, size=2, wait=10, human=60000)
        workflow.scenarios[0].parameters.update(interarrival_time=.1 / 60,
            human_variation=0, system_variation=0)
        live = LiveSimulationSession(workflow, count=2, speed=1, staff_capacity=1)
        live.play()
        try:
            deadline = time.monotonic() + 2
            while live.snapshot()["state"] not in ("completed", "failed") and time.monotonic() < deadline:
                time.sleep(.005)
            snapshot = live.snapshot()
            self.assertEqual(snapshot["state"], "completed", snapshot.get("error"))
            self.assertAlmostEqual(snapshot["time"] * 60, .102)
            self.assertEqual(len(live.engine.completed_specimens), 2)
            self.assertEqual(snapshot["staff_available"], 1)
            self.assertEqual(snapshot["staff_assignments"], [])
        finally:
            live.stop()

    def test_reporting_and_analyzers_do_not_inherit_preparation_batching(self):
        _, node, _, _ = self.build()
        for kind in ("result reporting", "manual analyzer", "specimen registration"):
            node.widget.node_type = kind
            self.assertEqual(staff_batch_settings(node), (1, 0))


if __name__ == "__main__":
    unittest.main()
