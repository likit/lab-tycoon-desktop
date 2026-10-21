"""Order tests fan out to analyzers and converge before reporting."""
import unittest

from app.system.model import Specimen, Workflow
from app.system.simulation import WorkflowSimulationEngine, WorkflowSimulationSettings


class OrderTestCompletionTests(unittest.TestCase):
    def build_workflow(self, menus=(("CBC",), ("GLU",)), rates=(1, .5), terminal=False):
        workflow = Workflow()
        processing = workflow.create_node("Processing", 0)
        centrifuges = [workflow.create_connected_node(processing.node_id, f"Centrifuge {i}", 1,
            kind="equipment", metadata={"equipment_id": f"CENT-{i}"}) for i in range(2)]
        analyzers = [workflow.create_node(f"Analyzer {i}", rates[i], kind="equipment",
            metadata={"equipment_id": f"CHEM-{i}", "node_type": "automated analyzer",
                      "analyzer_tests": list(menu)}) for i, menu in enumerate(menus)]
        reporting = workflow.create_node("Reporting Station" if terminal else "Report", 0,
            kind="equipment" if terminal else "task",
            metadata={"terminal_output": terminal, "node_type": "result reporting"})
        for centrifuge in centrifuges:
            for analyzer in analyzers:
                workflow.graph.connect_nodes(centrifuge.node_id, analyzer.node_id)
        for analyzer in analyzers:
            workflow.graph.connect_nodes(analyzer.node_id, reporting.node_id)
        return workflow, analyzers, reporting

    def engine(self, workflow, tests=("CBC", "GLU"), count=1, **kwargs):
        specimens = [Specimen(str(i), metadata={"order_id": str(i),
            "all_tests": list(tests), "pending_tests": list(tests)}) for i in range(count)]
        return WorkflowSimulationEngine(workflow, specimens=specimens,
            settings=WorkflowSimulationSettings(specimens_number=count,
                system_variation=0, human_variation=0, staff_capacity=2), **kwargs)

    def test_slow_arrivals_share_idle_equipment_even_with_different_speeds(self):
        workflow, analyzers, _ = self.build_workflow(menus=(("CBC",), ("CBC",)), rates=(1200, 200))
        engine = self.engine(workflow, tests=("CBC",), count=6)
        engine.settings.interarrival_time = 10
        result = engine.run()
        for node in workflow.graph.iter_nodes():
            if node.kind == "equipment":
                self.assertEqual(result.node_stats[node.node_id].specimens_processed, 3)
        self.assertEqual(len(result.completed_specimens), 6)
        self.assertTrue(all(not specimen.metadata["pending_tests"] for specimen in result.completed_specimens))

    def test_generated_orders_have_tests_and_exercise_both_analyzers(self):
        workflow, analyzers, _ = self.build_workflow(menus=(("CBC",), ("CBC",)), rates=(1200, 200))
        processing = next(node for node in workflow.graph.iter_nodes() if node.widget.name == "Processing")
        adapter = workflow.create_node("Orders", kind="order_adapter",
            metadata={"node_type": "order adapter", "adapter_type": "simulation"})
        workflow.graph.connect_nodes(adapter.node_id, processing.node_id)
        created = []
        engine = WorkflowSimulationEngine(workflow, on_order_created=created.append,
            settings=WorkflowSimulationSettings(specimens_number=6, interarrival_time=10,
                system_variation=0, human_variation=0))
        result = engine.run()
        self.assertEqual(len(created), 6)
        self.assertTrue(all([item.test.code for item in order.order_items] == ["CBC"] for order in created))
        for node in analyzers:
            self.assertEqual(result.node_stats[node.node_id].specimens_processed, 3)
        self.assertTrue(all(specimen.metadata["completed_tests"] == ["CBC"]
                            for specimen in result.completed_specimens))

    def test_generated_orders_use_explicit_simulation_test_for_unrestricted_analyzers(self):
        workflow, _, _ = self.build_workflow(menus=((), ()), rates=(1, 1))
        processing = next(node for node in workflow.graph.iter_nodes() if node.widget.name == "Processing")
        adapter = workflow.create_node("Orders", kind="order_adapter",
            metadata={"node_type": "order adapter", "adapter_type": "simulation"})
        workflow.graph.connect_nodes(adapter.node_id, processing.node_id)
        result = WorkflowSimulationEngine(workflow,
            settings=WorkflowSimulationSettings(specimens_number=2, system_variation=0)).run()
        self.assertTrue(all(specimen.metadata["completed_tests"] == ["SIM-TEST"]
                            for specimen in result.completed_specimens))

    def test_parallel_tests_join_before_reporting_and_complete_once(self):
        workflow, analyzers, reporting = self.build_workflow()
        notifications = []
        activities = []
        result = self.engine(workflow, on_specimen_completed=lambda *args: notifications.append(args),
                             on_activity=activities.append).run()
        self.assertEqual(result.makespan, 3)  # centrifuge 1 + max(analyzers 1, 2)
        self.assertEqual(notifications, [(1, 1)])
        self.assertEqual(len(result.completed_specimens), 1)
        specimen = result.completed_specimens[0]
        self.assertEqual(specimen.metadata["pending_tests"], [])
        self.assertCountEqual(specimen.metadata["completed_tests"], ["CBC", "GLU"])
        finished = [event for event in specimen.history if event["event"] == "test_finished"]
        self.assertCountEqual([event["details"]["test_code"] for event in finished], ["CBC", "GLU"])
        starts = [event["details"]["time"] for event in specimen.history if event["event"] == "test_started"]
        self.assertEqual(starts, [1, 1])
        report_arrival = next(event for event in activities
                              if event["node_id"] == reporting.node_id and event["event"] == "arrived")
        self.assertGreaterEqual(report_arrival["time"], max(event["details"]["time"] for event in finished))
        self.assertEqual(result.node_stats[reporting.node_id].specimens_processed, 1)
        for node in analyzers:
            self.assertEqual(result.node_stats[node.node_id].specimens_processed, 1)

    def test_orders_do_not_share_results_and_each_reports_once(self):
        workflow, _, reporting = self.build_workflow()
        engine = self.engine(workflow, count=2)
        engine.specimens[1].metadata.update(all_tests=["CBC"], pending_tests=["CBC"])
        result = engine.run()
        self.assertEqual(len(result.completed_specimens), 2)
        self.assertEqual(result.node_stats[reporting.node_id].specimens_processed, 2)
        for specimen in result.completed_specimens:
            self.assertCountEqual(specimen.metadata["completed_tests"], specimen.metadata["all_tests"])
            self.assertEqual(sum(event["event"] == "completed" for event in specimen.history), 1)

    def test_overlapping_menus_do_not_duplicate_test_work(self):
        workflow, analyzers, _ = self.build_workflow(menus=(("CBC", "GLU"), ("CBC", "GLU")), rates=(1, 1))
        result = self.engine(workflow).run()
        self.assertEqual(result.makespan, 2)
        self.assertEqual(sum(result.node_stats[node.node_id].specimens_processed for node in analyzers), 2)
        finished = [event["details"]["test_code"] for event in result.completed_specimens[0].history
                    if event["event"] == "test_finished"]
        self.assertCountEqual(finished, ["CBC", "GLU"])

    def test_unsupported_test_stops_run_without_reporting(self):
        workflow, _, reporting = self.build_workflow()
        engine = self.engine(workflow, tests=("UNKNOWN",))
        with self.assertRaisesRegex(ValueError, "Order 0.*supports test UNKNOWN"):
            engine.run()
        self.assertEqual(engine.completed_specimens, [])
        self.assertEqual(engine.node_stats[reporting.node_id].specimens_processed, 0)

    def test_disabled_or_zero_capacity_analyzer_cannot_complete_required_tests(self):
        for metadata in ({"enabled": False}, {"throughput": 0, "service_time": 0}):
            with self.subTest(metadata=metadata):
                workflow, analyzers, _ = self.build_workflow()
                if "enabled" in metadata:
                    analyzers[1].widget.enabled = False
                else:
                    analyzers[1].widget.service_time = 0
                engine = self.engine(workflow)
                with self.assertRaisesRegex(ValueError, "supports test GLU"):
                    engine.run()
                self.assertEqual(engine.completed_specimens, [])

    def test_terminal_station_receives_report_only_after_join(self):
        workflow, _, reporting = self.build_workflow(terminal=True)
        result = self.engine(workflow).run()
        received = [event for event in result.completed_specimens[0].history if event["event"] == "report_received"]
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["details"]["time"], 3)
        self.assertEqual(result.node_stats[reporting.node_id].specimens_processed, 0)

    def test_sequential_analyzers_still_process_remaining_tests(self):
        workflow = Workflow()
        first = workflow.create_node("CBC", 1, metadata={"node_type": "automated analyzer", "analyzer_tests": ["CBC"]})
        second = workflow.create_connected_node(first.node_id, "GLU", .5,
            metadata={"node_type": "automated analyzer", "analyzer_tests": ["GLU"]})
        workflow.create_connected_node(second.node_id, "Reporting", 0)
        result = self.engine(workflow).run()
        self.assertEqual(result.makespan, 3)
        self.assertCountEqual(result.completed_specimens[0].metadata["completed_tests"], ["CBC", "GLU"])

    def test_reporting_and_end_of_workflow_block_unfinished_orders(self):
        for terminal in (False, True):
            with self.subTest(terminal=terminal):
                workflow = Workflow()
                intake = workflow.create_node("Intake", 0)
                first = workflow.create_connected_node(intake.node_id, "CBC", 1,
                    metadata={"node_type": "automated analyzer", "analyzer_tests": ["CBC"]})
                if terminal:
                    workflow.create_connected_node(first.node_id, "Reporting Station", 0,
                        metadata={"terminal_output": True})
                engine = self.engine(workflow)
                with self.assertRaisesRegex(ValueError, "Order 0.*unfinished tests: GLU"):
                    engine.run()
                self.assertEqual(engine.completed_specimens, [])
                self.assertTrue(any(event["event"] == "report_blocked" for event in engine.specimens[0].history))

    def test_live_parallel_routes_start_at_centrifuge_and_converge_at_station(self):
        from app.system.live_simulation import LiveSimulationSession

        workflow, analyzers, reporting = self.build_workflow(terminal=True)
        reporting.metadata["equipment_id"] = "REPORT-001"
        live = LiveSimulationSession(workflow, count=1)
        live.engine.specimens = self.engine(workflow).specimens
        observed = []
        def observe(event):
            live._activity(event)
            if event["event"] == "arrived" and event["node_id"] in {
                    analyzers[0].node_id, analyzers[1].node_id, reporting.node_id}:
                observed.append((event, live.snapshot()["transfers"]))
        live.engine.on_activity = observe
        live.engine.run()
        for event, transfers in observed:
            if event["node_id"] != reporting.node_id:
                self.assertEqual(len(event["source_equipment_ids"]), 1)
                self.assertTrue(event["source_equipment_ids"][0].startswith("CENT-"))
            else:
                self.assertCountEqual(event["source_equipment_ids"], ["CHEM-0", "CHEM-1"])
                self.assertCountEqual([transfer["source"] for transfer in transfers
                    if transfer["target"] == "REPORT-001"], ["CHEM-0", "CHEM-1"])

    def test_manual_analyzers_use_staff_and_release_it_before_reporting(self):
        workflow, analyzers, _ = self.build_workflow()
        for node in analyzers:
            node.widget.node_type = "manual analyzer"
            node.widget.human_throughput = 1
            node.widget.required_staff = 1
        engine = self.engine(workflow)
        result = engine.run()
        self.assertEqual(result.makespan, 2)
        self.assertEqual(engine.staff_pool.level, 2)
        self.assertEqual(len(result.completed_specimens), 1)


if __name__ == "__main__":
    unittest.main()
