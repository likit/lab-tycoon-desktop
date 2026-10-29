"""The built-in demo runs immediately and survives project serialization."""
from pathlib import Path
import tempfile
import unittest

from app.demo_project import create_demo_project
from app.project_serializer import ProjectSerializer
from app.system.live_simulation import LiveSimulationSession


class DemoProjectTests(unittest.TestCase):
    def test_floor_play_after_saving_analyzer_settings(self):
        from app.system.workflow_editor import WorkflowEditorController
        from app.system.storage import WorkflowDocument

        project = create_demo_project()
        controller = WorkflowEditorController(
            current_document=WorkflowDocument('demo', 'Demo', project.workflow),
            on_change=lambda: None, equipment_instances=project.floor.equipment_instances)
        analyzer = next(node for node in project.workflow.graph.iter_nodes()
                        if node.metadata.get('equipment_id') == 'CHEM-001')
        controller.selected_node_id = analyzer.node_id
        controller.bind_selected_equipment('CHEM-001')
        self.assertEqual(analyzer.widget.throughput, 20)
        live = LiveSimulationSession(project.workflow,
            scenario=project.workflow.active_scenario, staff_capacity=1,
            count=10, seed=7, speed=100,
            equipment_types={e.instance_id: e.equipment_type
                             for e in project.floor.equipment_instances})
        live.play()
        try:
            live._thread.join(timeout=3)
            self.assertEqual(live.state, 'completed', live.error)
            self.assertEqual(len(live.engine.completed_specimens), 10)
            self.assertEqual(len(live.snapshot()['results_feed']), 10)
        finally:
            live.stop()

    def test_saved_demo_completes_ten_orders_with_one_staff_member(self):
        project = create_demo_project()
        self.assertTrue(project.is_dirty)
        with tempfile.TemporaryDirectory() as directory:
            path = ProjectSerializer().save(project, Path(directory) / 'demo.ltp')
            project = ProjectSerializer().load(path)
        self.assertEqual(project.workflow.active_scenario.workforce,
                         [{'id': 'demo-staff', 'name': 'Alex'}])
        self.assertEqual(project.workflow.active_scenario.parameters['staff_capacity'], 1)
        live = LiveSimulationSession(project.workflow, count=10, seed=7,
            equipment_types={e.instance_id: e.equipment_type for e in project.floor.equipment_instances})
        events = []
        original = live.engine.on_activity
        def record(event):
            events.append(event)
            original(event)
        live.engine.on_activity = record
        live.engine.run()
        self.assertEqual(len(live.engine.completed_specimens), 10)
        self.assertEqual(len(live.snapshot()['results_feed']), 10)
        times = [order.order_datetime for order in live.generated_orders]
        self.assertEqual(len(times), 10)
        for previous, current in zip(times, times[1:]):
            self.assertEqual((current - previous).total_seconds(), 60)
        self.assertGreater(live.engine.env.now, 0)
        acquired = [event for event in events if event['event'] == 'staff_acquired']
        self.assertTrue(acquired)
        self.assertTrue(all(event['staff_names'] == ['Alex'] for event in acquired))
        for specimen in live.engine.completed_specimens:
            self.assertEqual(specimen.metadata['completed_tests'], ['TG'])

    def test_reporting_then_validation_then_terminal_station(self):
        from app.system.lis_analysis import result_stage
        project = create_demo_project()
        nodes = list(project.workflow.graph.iter_nodes())
        reporting = next(n for n in nodes if n.widget.node_type == 'result reporting')
        validation = next(n for n in nodes if n.widget.node_type == 'result validating')
        terminal = next(n for n in nodes if n.metadata.get('terminal_output'))
        self.assertEqual([e.target for e in project.workflow.graph.outgoing_edges(reporting.node_id)],
                         [validation.node_id])
        self.assertEqual([e.target for e in project.workflow.graph.outgoing_edges(validation.node_id)],
                         [terminal.node_id])
        self.assertEqual(result_stage(validation), 'approver')
        live = LiveSimulationSession(project.workflow, count=2, seed=7,
            equipment_types={e.instance_id: e.equipment_type for e in project.floor.equipment_instances})
        result = live.engine.run()
        for specimen in result.completed_specimens:
            departures = {e['widget']: e['details']['time'] for e in specimen.history if e['event'] == 'departed'}
            self.assertLess(departures['Result Reporting'], departures['Result Validation'])

    def test_demos_are_independent(self):
        first, second = create_demo_project(), create_demo_project()
        first.workflow.active_scenario.workforce[0]['name'] = 'Changed'
        self.assertEqual(second.workflow.active_scenario.workforce[0]['name'], 'Alex')
        self.assertNotEqual(first.project_id, second.project_id)
