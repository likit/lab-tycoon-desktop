"""Selected LIS work uses the same paced session and floor as generated work."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.project import LabTycoonProject, ProjectEquipmentInstance
from app.system.analysis_floor import LISAnalysisSession, analysis_floor
from app.system.model import Specimen, Workflow
from app.system.simulation import WorkflowSimulationEngine, WorkflowSimulationSettings
from app.system.floor_view import FloorViewRenderer
from app.system.live_simulation import LiveSimulationSession, simulation_progress
from app.system.models import LabOrderItem, LISWorkflowAnalysis
from tests.test_live_simulation import wait_for


class AnalysisFloorTests(unittest.TestCase):
    def setUp(self):
        from tests.test_lis_analysis import LISAnalysisTests
        LISAnalysisTests.setUp(self)

    def analysis(self, ids=None, speed=2):
        live = LISAnalysisSession(ids or [self.ids[0]], workflow_document=self.document,
            operator_username='analyst', database_engine=self.engine, random_seed=1, speed=speed)
        self.addCleanup(live.wait)
        self.addCleanup(live.stop)
        return live

    def test_selected_test_runs_on_shared_worker_and_saves_only_after_completion(self):
        live = self.analysis()
        snapshots = []
        execute = LiveSimulationSession._execute_engine
        def observe(session):
            snapshots.append(session.snapshot())
            return execute(session)
        with patch.object(LiveSimulationSession, '_execute_engine', observe), \
             patch.object(WorkflowSimulationEngine, 'run', side_effect=AssertionError('Unpaced execution')):
            live.play()
            wait_for(lambda: live.state in ('completed', 'failed'))
            live.wait()
        self.assertEqual(live.state, 'completed', live.error)
        self.assertEqual(live.result.analyzed, 1)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(live.snapshot()['orders_total'], 1)
        self.assertEqual(live.snapshot()['tests_total'], 1)
        self.assertEqual(live.snapshot()['completed'], 1)
        self.assertEqual(live.generated_orders, [])
        self.assertAlmostEqual(live.snapshot()['time'], live.result.makespan)
        with Session(self.engine) as session:
            self.assertIsNotNone(session.get(LabOrderItem, self.ids[0]).finished_at)
            self.assertIsNone(session.get(LabOrderItem, self.ids[1]).finished_at)
            self.assertIsNotNone(session.get(LISWorkflowAnalysis, live.result.run_id))

    def test_shared_pause_stop_and_wait_roll_back_selected_tests(self):
        live = self.analysis(speed=.001)
        live.play()
        wait_for(lambda: live.prepared and bool(live.snapshot()['counts']))
        live.pause()
        before = live.snapshot()['time']
        time.sleep(.03)
        self.assertEqual(live.snapshot()['time'], before)
        self.assertEqual(live.state, 'paused')
        live.stop()
        live.wait()
        self.assertEqual(live.state, 'cancelled')
        self.assertIsNone(live.result)
        with Session(self.engine) as session:
            self.assertTrue(all(item.finished_at is None for item in session.scalars(select(LabOrderItem))))
            self.assertEqual(session.scalar(select(func.count()).select_from(LISWorkflowAnalysis)), 0)

    def test_live_analysis_tracks_two_order_specimens_and_four_test_results(self):
        live = self.analysis(self.ids)
        live.play()
        wait_for(lambda: live.state in ('completed', 'failed'))
        live.wait()
        self.assertEqual(live.state, 'completed', live.error)
        snapshot = live.snapshot()
        self.assertEqual((snapshot['total'], snapshot['completed']), (2, 2))
        self.assertEqual((snapshot['orders_total'], snapshot['orders_completed']), (2, 2))
        self.assertEqual((snapshot['tests_total'], snapshot['tests_completed']), (4, 4))
        self.assertEqual(live.engine.node_stats[self.reception.node_id].specimens_processed, 2)
        self.assertEqual(live.result.analyzed, 4)
        self.assertEqual(live.generated_orders, [])

    def test_worker_failure_does_not_publish_success_or_save_results(self):
        self.analyzer.metadata['analyzer_tests'] = ['unsupported']
        self.analyzer.widget.metadata['analyzer_tests'] = ['unsupported']
        live = self.analysis()
        live.play()
        wait_for(lambda: live.state in ('completed', 'failed'))
        live.wait()
        self.assertEqual(live.state, 'failed')
        self.assertTrue(live.error)
        self.assertIsNone(live.result)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(LabOrderItem, self.ids[0]).finished_at)
            self.assertEqual(session.scalar(select(func.count()).select_from(LISWorkflowAnalysis)), 0)

    def test_temporary_layout_preserves_project_and_workflow(self):
        project = LabTycoonProject.create('Demo')
        project.floor.equipment_instances.append(ProjectEquipmentInstance('existing', 'centrifuge', 1, 1))
        original = self.document.workflow.to_dict()
        workflow, floor = analysis_floor(self.document.workflow, project)
        self.assertEqual(self.document.workflow.to_dict(), original)
        self.assertEqual(len(project.floor.equipment_instances), 1)
        self.assertGreater(len(floor.equipment_instances), 1)
        self.assertTrue(all(item.col >= project.floor.columns for item in floor.equipment_instances[1:]))
        self.assertTrue(any(node.metadata.get('equipment_id') for node in workflow.graph.iter_nodes()))

    def test_display_placements_do_not_force_attending_steps_to_share_capacity(self):
        from tests.test_lis_analysis import LISAnalysisTests
        report, validation, _ = LISAnalysisTests.add_validation(self)
        report.widget.capacity = 2
        validation.widget.capacity = 3
        for node in (report, validation):
            node.metadata['attend_equipment_id'] = 'REPORT'
        self.analyzer.kind = 'equipment'
        self.analyzer.metadata.update(equipment_id='CHEM', equipment_type='chemistry_analyzer')
        self.analyzer.widget.capacity = 50
        project = LabTycoonProject.create('Different process capacities')
        project.floor.equipment_instances.extend([
            ProjectEquipmentInstance('CHEM', 'chemistry_analyzer', 2, 2),
            ProjectEquipmentInstance('REPORT', 'reporting_station', 6, 6)])
        original = self.document.workflow.to_dict()
        live = LISAnalysisSession([self.ids[0]], workflow_document=self.document,
            operator_username='analyst', project=project, database_engine=self.engine,
            random_seed=1, speed=2)
        self.addCleanup(live.wait)
        self.addCleanup(live.stop)

        def check_resources():
            self.assertEqual(live.engine.node_resources[self.analyzer.node_id].capacity, 50)
            self.assertEqual(live.engine.node_resources[report.node_id].capacity, 2)
            self.assertEqual(live.engine.node_resources[validation.node_id].capacity, 3)
            self.assertIsNot(live.engine.node_resources[report.node_id],
                             live.engine.node_resources[validation.node_id])
            self.assertNotEqual(live.engine.workflow.get_node(report.node_id).kind, 'equipment')

        check_resources()
        live.play()
        wait_for(lambda: live.state in ('completed', 'failed'))
        live.wait()
        self.assertEqual(live.state, 'completed', live.error)
        self.assertEqual(live.result.approved, 1)
        check_resources()
        self.assertEqual(self.document.workflow.to_dict(), original)

    def test_ten_orders_with_nine_tests_each_have_shared_order_and_test_progress(self):
        tests = [f'TEST-{index}' for index in range(9)]
        specimens = [Specimen(str(order), metadata={'order_id': str(order), 'all_tests': list(tests),
            'completed_tests': list(tests) if order == 0 else (tests[:8] if order == 1 else [])})
            for order in range(10)]
        engine = SimpleNamespace(specimens=specimens, completed_specimens=specimens[:1])
        self.assertEqual(simulation_progress(engine), dict(
            orders_total=10, orders_completed=1, tests_total=90, tests_completed=17))
        for specimen in specimens:
            specimen.metadata['completed_tests'] = list(tests)
        engine.completed_specimens = specimens
        self.assertEqual(simulation_progress(engine), dict(
            orders_total=10, orders_completed=10, tests_total=90, tests_completed=90))

    def test_orders_floor_displays_twenty_occupied_slots_on_fifty_capacity_analyzer(self):
        for placed in (False, True):
            with self.subTest(placed=placed):
                workflow = Workflow.from_dict(self.document.workflow.to_dict())
                analyzer = workflow.get_node(self.analyzer.node_id)
                analyzer.widget.capacity = 50
                if placed:
                    analyzer.kind = 'equipment'
                    analyzer.metadata.update(equipment_id='CHEM-001', equipment_type='chemistry_analyzer')
                settings = WorkflowSimulationSettings.from_scenario(
                    specimens_number=20, workflow=workflow, scenario=workflow.active_scenario)
                engine = WorkflowSimulationEngine(workflow, settings=settings)
                visual, floor = analysis_floor(engine.workflow)
                live = LiveSimulationSession(visual, count=20, simulation_engine=engine,
                    equipment_types={item.instance_id: item.equipment_type for item in floor.equipment_instances})
                live.state = 'playing'
                equipment_id = live.workflow.get_node(analyzer.node_id).metadata['equipment_id']
                resource = engine.node_resources[analyzer.node_id]
                requests = [resource.request() for _ in range(20)]
                self.assertEqual(live.snapshot()['equipment_capacity'][equipment_id], dict(used=20, total=50))
                renderer = FloorViewRenderer.__new__(FloorViewRenderer)
                renderer.floor_plan, renderer.workflow, renderer.live_session = floor, live.workflow, live
                renderer.canvas = Mock()
                renderer.canvas.bbox.return_value = (100, 120, 356, 376)
                renderer._equipment_canvas_items = {equipment_id: 1}
                renderer.render_analyzer_capacity()
                self.assertEqual(renderer.canvas.create_text.call_args.kwargs['text'], 'Capacity 20/50')
                live.state = 'paused'
                renderer.render_analyzer_capacity()
                self.assertEqual(renderer.canvas.create_text.call_args.kwargs['text'], 'Capacity 20/50')
                for request in requests:
                    resource.release(request)
                renderer.render_analyzer_capacity()
                self.assertEqual(renderer.canvas.create_text.call_args.kwargs['text'], 'Capacity 0/50')
