"""Paced real-engine lifecycle, event traces and persistence."""
import tempfile
import time
import unittest
from unittest.mock import Mock
from pathlib import Path
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from app.system.model import Workflow
from app.system.live_simulation import LiveSimulationSession
from app.system.simulation import run_workflow_simulation
from app.system.storage import WorkflowDocument
from app.simulation_runs import persist_generated_run
from app.system.models import Base, SimulationRun, SimulationOrder, LabOrder


def workflow():
    model = Workflow()
    source = model.create_node('Orders', kind='order_adapter', metadata={'node_type': 'order adapter', 'adapter_type': 'simulation'})
    analyzer = model.create_connected_node(source.node_id, 'Analyzer', 5, kind='equipment',
        metadata={'equipment_id': 'CHEM-001', 'output_data': True})
    report = model.create_connected_node(analyzer.node_id, 'Reporting', 5)
    validate = model.create_connected_node(report.node_id, 'Validating', 5)
    station = model.create_connected_node(validate.node_id, 'Report Station', kind='equipment',
        metadata={'equipment_id': 'REPORT-001', 'terminal_output': True})
    return model, analyzer, station


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for simulation')
        time.sleep(.005)


class LiveSimulationTests(unittest.TestCase):
    def test_equipment_bound_to_analyzer_processes_selected_tests_and_sends_them(self):
        model, analyzer, station = workflow()
        analyzer.widget.node_type = 'equipment'
        analyzer.metadata['node_type'] = 'equipment'
        analyzer.widget.metadata['analyzer_tests'] = ['GLU', 'ALT']
        analyzer.metadata['analyzer_tests'] = ['GLU', 'ALT']
        live = LiveSimulationSession(model, count=3, seed=7,
            equipment_types={'CHEM-001': 'chemistry_analyzer', 'REPORT-001': 'reporting_station'})
        live.engine.run()
        feed = live.snapshot()['results_feed']
        self.assertEqual(len(feed), 3)
        self.assertTrue(all(set(entry['tests']) == {'GLU', 'ALT'} for entry in feed))
        self.assertTrue(all(not specimen.metadata['pending_tests']
                            for specimen in live.engine.completed_specimens))
        self.assertEqual(model.get_node(analyzer.node_id).widget.node_type, 'equipment')
        self.assertEqual(live.workflow.get_node(analyzer.node_id).widget.node_type, 'automated analyzer')

    def test_legacy_generic_analyzers_show_data_without_inventing_test_details(self):
        model, analyzer, station = workflow()
        self.assertNotIn(analyzer.widget.node_type, ('automated analyzer', 'manual analyzer'))
        live = LiveSimulationSession(model, count=3, seed=7,
            equipment_types={'CHEM-001': 'chemistry_analyzer', 'REPORT-001': 'reporting_station'})
        live.engine.run()
        feed = live.snapshot()['results_feed']
        self.assertEqual(len(feed), 3)
        self.assertTrue(all(entry['tests'] == [] for entry in feed))
        self.assertTrue(all(entry['sources'] == ['CHEM-001'] for entry in feed))

    def test_reporting_task_at_station_records_arrival_before_processing_and_deduplicates_terminal(self):
        model, analyzer, station = workflow()
        report = next(node for node in model.graph.iter_nodes() if node.widget.name == 'Reporting')
        report.widget.node_type = 'result reporting'
        report.metadata['attend_equipment_id'] = 'REPORT-001'
        live = LiveSimulationSession(model, count=1, seed=7,
            equipment_types={'CHEM-001': 'chemistry_analyzer', 'REPORT-001': 'reporting_station'})
        live.engine.run()
        feed = live.snapshot()['results_feed']
        self.assertEqual(len(feed), 1)
        specimen = live.engine.completed_specimens[0]
        terminal_arrival = next(event['details']['time'] for event in specimen.history
                                if event.get('event') == 'arrived' and event.get('widget') == 'Report Station')
        self.assertLess(feed[0]['time'], terminal_arrival)

    def test_reporting_feed_contains_real_completed_tests_once_and_is_isolated(self):
        model, analyzer, station = workflow()
        analyzer.widget.node_type = 'automated analyzer'
        analyzer.widget.metadata['analyzer_tests'] = ['GLU', 'ALT']
        live = LiveSimulationSession(model, count=2, seed=7, speed=10,
            equipment_types={'CHEM-001': 'chemistry_analyzer', 'REPORT-001': 'reporting_station'})
        live.engine.run()
        feed = live.snapshot()['results_feed']
        self.assertEqual(len(feed), 2)
        self.assertEqual([entry['sequence'] for entry in feed], [1, 2])
        self.assertEqual(len({entry['order_id'] for entry in feed}), 2)
        for entry in feed:
            self.assertEqual(set(entry['tests']), {'GLU', 'ALT'})
            self.assertEqual(entry['sources'], ['CHEM-001'])
            self.assertEqual(entry['target'], 'REPORT-001')
        feed[0]['tests'].append('MUTATED')
        self.assertNotIn('MUTATED', live.snapshot()['results_feed'][0]['tests'])
        self.assertEqual(LiveSimulationSession(model, count=1).snapshot()['results_feed'], [])

    def test_reporting_feed_ignores_non_analyzer_and_duplicate_transfers(self):
        model, analyzer, station = workflow()
        live = LiveSimulationSession(model, count=1,
            equipment_types={'CHEM-001': 'chemistry_analyzer', 'REPORT-001': 'reporting_station'})
        event = dict(event='arrived', node_id=station.node_id, specimen_id='S1',
                     order_id='ORDER-1', time=1, completed_tests=['GLU'],
                     source_equipment_ids=['CENT-001'])
        live._activity(event)
        self.assertEqual(live.snapshot()['results_feed'], [])
        event['source_equipment_ids'] = ['CHEM-001']
        live._activity(event)
        live._activity(event)
        self.assertEqual(len(live.snapshot()['results_feed']), 1)

    def test_real_queue_pause_resume_stop_and_no_worker_ui_calls(self):
        model, analyzer, station = workflow()
        live = LiveSimulationSession(model, count=3, seed=7, speed=1)
        live.play()
        wait_for(lambda: live.snapshot()['counts'].get(analyzer.node_id, {}).get('active') == 1)
        live.pause()
        paused = live.snapshot()
        self.assertEqual(paused['counts'][analyzer.node_id]['queued'], 2)
        time.sleep(.04)
        self.assertEqual(live.snapshot()['time'], paused['time'])
        self.assertEqual(live.snapshot()['counts'], paused['counts'])
        live.play()
        wait_for(lambda: bool(live.snapshot()['transfers']))
        self.assertEqual(live.snapshot()['transfers'][0]['target'], 'REPORT-001')
        live.stop()
        live._thread.join(timeout=1)
        self.assertEqual(live.state, 'cancelled')
        self.assertFalse(live._thread.is_alive())

    def test_completed_run_matches_batch_engine_and_persists_once(self):
        model, analyzer, station = workflow()
        baseline = run_workflow_simulation(specimens_number=3,
            workflow_document=WorkflowDocument.create('Workflow', workflow=model), random_seed=7)
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine('sqlite:///' + str(Path(directory) / 'live.db'))
            Base.metadata.create_all(engine)
            calls = []
            def persist(live):
                calls.append(True)
                return persist_generated_run(project_id='project', scenario=live.scenario,
                    workflow_name='Workflow', requested_orders=3, random_seed=7,
                    generated_orders=live.generated_orders, database_engine=engine)
            live = LiveSimulationSession(model, count=3, seed=7, speed=100, on_complete=persist)
            live.play()
            wait_for(lambda: live.state in ('completed', 'failed'))
            self.assertEqual(live.state, 'completed', live.error)
            self.assertEqual(live.snapshot()['completed'], 3)
            self.assertAlmostEqual(live.elapsed, baseline.makespan)
            self.assertEqual(live.snapshot()['counts'][station.node_id]['finished'], 3)
            self.assertEqual(calls, [True])
            self.assertEqual(len(live.generated_orders), 3)
            with Session(engine) as db:
                self.assertEqual(db.scalar(select(func.count()).select_from(SimulationRun)), 1)
                self.assertEqual(db.scalar(select(func.count()).select_from(SimulationOrder)), 3)
                self.assertEqual(db.scalar(select(func.count()).select_from(LabOrder)), 0)
            engine.dispose()

    def test_snapshot_isolated_from_workflow_edits_and_cancel_never_saves(self):
        model, analyzer, _ = workflow()
        calls = []
        live = LiveSimulationSession(model, count=2, speed=.01, on_complete=lambda s: calls.append(s))
        model.graph.remove_node(analyzer.node_id)
        self.assertIsNotNone(live.workflow.get_node(analyzer.node_id))
        live.play()
        live.stop()
        live._thread.join(timeout=1)
        self.assertEqual(calls, [])

    def test_reopening_floor_restores_the_same_paused_session(self):
        from app.system.floor_view import FloorViewRenderer
        model, analyzer, _ = workflow()
        live = LiveSimulationSession(model, count=2, speed=1)
        live.play()
        wait_for(lambda: live.snapshot()['counts'].get(analyzer.node_id, {}).get('active') == 1)
        live.pause()
        before = live.snapshot()
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.live_session = live
        renderer.workflow = live.workflow
        from app.system.floor_playback import FloorPlayback
        renderer.playback = FloorPlayback()
        renderer.sync_live_session()
        self.assertEqual(renderer.playback.state, 'paused')
        self.assertEqual(renderer.live_counts, before['counts'])
        self.assertTrue(renderer.equipment_activity('CHEM-001'))
        self.assertFalse(renderer.equipment_activity('REPORT-001'))
        self.assertEqual(live.snapshot()['time'], before['time'])
        live.stop()
        live._thread.join(timeout=1)

    def test_project_close_cancels_the_owned_session(self):
        from app.project import LabTycoonProject, ProjectSession
        project = LabTycoonProject.create('Lab')
        project.live_simulation = Mock()
        owner = ProjectSession()
        owner.activate(project)
        owner.close()
        project.live_simulation.stop.assert_called_once()

    def test_available_staff_override_and_scenario_default(self):
        model, analyzer, _ = workflow()
        model.scenarios[0].parameters['staff_capacity'] = 4
        live = LiveSimulationSession(model, staff_capacity=2)
        self.assertEqual(live.engine.staff_capacity, 2)
        self.assertEqual(live.snapshot()['staff_available'], 2)
        self.assertEqual(live.scenario.parameters['staff_capacity'], 2)
        self.assertEqual(model.scenarios[0].parameters['staff_capacity'], 4)
        inherited = LiveSimulationSession(model)
        self.assertEqual(inherited.engine.staff_capacity, 4)
        analyzer.widget.required_staff = 3
        analyzer.widget.human_throughput = 5
        analyzer.metadata.update(required_staff=3, human_throughput=5)
        with self.assertRaisesRegex(ValueError, 'available staff capacity'):
            LiveSimulationSession(model, staff_capacity=2)
        for invalid in (0, -1, 1.5, '2'):
            with self.assertRaises(ValueError):
                LiveSimulationSession(model, staff_capacity=invalid)

    def test_staff_attendance_follows_real_acquisition_and_release(self):
        model, analyzer, station = workflow()
        analyzer.widget.required_staff = 2
        analyzer.widget.human_throughput = 4
        analyzer.metadata.update(required_staff=2, human_throughput=4)
        reporting = next(node for node in model.graph.iter_nodes() if node.widget.name == 'Reporting')
        validating = next(node for node in model.graph.iter_nodes() if node.widget.name == 'Validating')
        for node in (reporting, validating):
            node.widget.required_staff = 1
            node.widget.human_throughput = 4
            node.metadata.update(required_staff=1, human_throughput=4)
        live = LiveSimulationSession(model, count=2, seed=3, staff_capacity=2,
            equipment_types={'CHEM-001': 'chemistry_analyzer', 'REPORT-001': 'reporting_station'})
        observed = []
        def observe(event):
            live._activity(event)
            assignments = live.snapshot()['staff_assignments']
            self.assertLessEqual(sum(item['staff_count'] for item in assignments), 2)
            if event['event'] == 'staff_acquired':
                observed.extend(item for item in assignments
                    if item['node_id'] == event['node_id'] and item['specimen_id'] == event['specimen_id'])
            if event['event'] == 'staff_released':
                self.assertFalse(any(item['node_id'] == event['node_id']
                    and item['specimen_id'] == event['specimen_id'] for item in assignments))
        live.engine.on_activity = observe
        live.engine.run()
        self.assertEqual(live.snapshot()['staff_assignments'], [])
        self.assertEqual(len(observed), 6)
        for allocation in observed:
            expected = 'CHEM-001' if allocation['node_id'] == analyzer.node_id else 'REPORT-001'
            self.assertEqual(allocation['equipment_id'], expected)
            self.assertIn(allocation['gender'], ('male', 'female'))
            self.assertEqual(allocation['staff_count'], 2 if expected == 'CHEM-001' else 1)

    def test_explicit_attendance_location_wins_over_default(self):
        model, _, _ = workflow()
        reporting = next(node for node in model.graph.iter_nodes() if node.widget.name == 'Reporting')
        reporting.metadata['attend_equipment_id'] = 'REPORT-002'
        live = LiveSimulationSession(model, equipment_types={'REPORT-001': 'reporting_station'})
        event = {'event': 'staff_acquired', 'node_id': reporting.node_id,
                 'specimen_id': 'SIM-O0001', 'staff_count': 1, 'time': 0}
        live._activity(event)
        self.assertEqual(live.snapshot()['staff_assignments'][0]['equipment_id'], 'REPORT-002')
        self.assertEqual(live.snapshot()['counts'], {})
        live._activity(dict(event, event='staff_released'))
        self.assertEqual(live.snapshot()['staff_assignments'], [])

    def test_invalid_speed_rejected(self):
        model, _, _ = workflow()
        for speed in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                LiveSimulationSession(model, speed=speed)
