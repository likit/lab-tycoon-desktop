"""Live floor summaries must distinguish machine slots, staff waits and batches."""
import unittest
from unittest.mock import Mock

from app.system.model import Workflow
from app.system.floor_status import equipment_overview, inspection_text
from app.system.live_simulation import LiveSimulationSession
from app.system.floor_view import EquipmentInspector, FloorViewRenderer, FloorPlan, EquipmentInstance


class FloorStatusTests(unittest.TestCase):
    def setUp(self):
        self.workflow = Workflow()
        self.source = self.workflow.create_node('Orders', kind='order_adapter',
            metadata={'node_type': 'order adapter', 'adapter_type': 'simulation'})
        self.analyzer = self.workflow.create_connected_node(self.source.node_id, 'Analyzer', 30,
            kind='equipment', metadata={'equipment_id': 'CHEM', 'node_type': 'automated analyzer',
                                       'capacity': 50, 'analyzer_tests': ['GLU']})
        self.snapshot = dict(state='playing', equipment_capacity={'CHEM': dict(used=20, total=50)},
            counts={self.analyzer.node_id: dict(queued=3, active=20)}, work_items=[dict(
                node_id=self.analyzer.node_id, specimen_id='1', order_id='O1', state='Processing',
                tests=['GLU', 'ALT'], completed_tests=['GLU'])])

    def summary(self):
        return equipment_overview(self.snapshot, self.workflow, {'CHEM': 'chemistry_analyzer'})['CHEM']

    def test_progress_uses_actual_test_interval_and_freezes_on_pause(self):
        live = LiveSimulationSession(self.workflow, count=1, seed=7)
        engine = live.engine
        arrivals = engine.env.process(engine._arrival_process())
        for _ in range(100):
            engine.env.step()
            if engine.active_processing:
                break
        self.assertTrue(engine.active_processing)
        interval = next(iter(engine.active_processing.values()))
        live.elapsed = (interval['start'] + interval['end']) / 2
        live.state = 'paused'
        work = next(job for job in live.snapshot()['work_items'] if job.get('progress'))
        progress = work['progress']
        self.assertAlmostEqual(progress['fraction'], .5)
        self.assertAlmostEqual(progress['remaining_seconds'],
                               (interval['end'] - interval['start']) * 1800)
        self.assertEqual(live.snapshot()['work_items'], live.snapshot()['work_items'])
        live.set_speed(10)
        self.assertEqual(next(job['progress'] for job in live.snapshot()['work_items']
                              if job.get('progress')), progress)
        engine.env.run(until=arrivals)
        self.assertFalse(engine.active_processing)
        self.assertFalse(live.snapshot()['work_items'])

    def test_parallel_progress_shows_next_completion_and_individual_order_details(self):
        first = self.snapshot['work_items'][0]
        first['progress'] = dict(fraction=.25, remaining_seconds=30, phase='Testing')
        self.snapshot['work_items'].append(dict(first, order_id='O2', specimen_id='2',
            progress=dict(fraction=.8, remaining_seconds=5, phase='Testing')))
        summary = self.summary()
        self.assertEqual(summary['progress'], dict(fraction=.8, remaining_seconds=5,
                                                  phase='Testing', concurrent=2))
        text = inspection_text('CHEM', summary)
        self.assertIn('Testing: 25% · 30s remaining', text)
        self.assertIn('Testing: 80% · 5s remaining', text)
        self.snapshot['state'] = 'completed'
        self.assertIsNone(self.summary()['progress'])

    def test_staff_wait_does_not_replace_or_add_to_occupied_machine_slots(self):
        step = self.workflow.create_connected_node(self.analyzer.node_id, 'Loading', 60,
            metadata={'attend_equipment_id': 'CHEM', 'capacity': 1})
        self.snapshot['staff_waiting'] = [dict(node_id=step.node_id, specimen_id='2')]
        summary = self.summary()
        self.assertEqual(summary['state'], 'Waiting for staff')
        self.assertEqual((summary['used'], summary['total']), (20, 50))
        self.assertEqual(summary['queued'], 3)
        text = inspection_text('CHEM', summary)
        self.assertIn('Occupied slots: 20/50', text)
        self.assertIn('Order O1 · Processing', text)
        self.assertIn('Requested: GLU, ALT', text)
        self.assertIn('Completed: GLU', text)

    def test_loading_unloading_full_and_stopped_states(self):
        for phase in ('Loading', 'Unloading'):
            self.snapshot['work_items'][0]['state'] = phase
            self.assertEqual(self.summary()['state'], phase)
        self.snapshot['work_items'][0]['state'] = 'Processing'
        self.snapshot['equipment_capacity']['CHEM']['used'] = 50
        self.assertEqual(self.summary()['state'], 'Full')
        self.snapshot['state'] = 'paused'
        self.assertEqual(self.summary()['state'], 'Full')
        self.snapshot['state'] = 'cancelled'
        summary = self.summary()
        self.assertEqual((summary['state'], summary['used'], summary['queued']), ('Idle', 0, 0))
        self.assertEqual(summary['jobs'], [])

    def test_batch_collection_has_actual_count_and_remaining_wait(self):
        preparation = self.workflow.create_connected_node(self.analyzer.node_id, 'Preparation', 60,
            metadata={'node_type': 'specimens processing', 'attend_equipment_id': 'CENT'})
        snapshot = dict(state='playing', counts={preparation.node_id: dict(queued=3)},
            batches={preparation.node_id: dict(collected=3, size=5, remaining_seconds=23)})
        summary = equipment_overview(snapshot, self.workflow, {'CENT': 'centrifuge'})['CENT']
        self.assertEqual(summary['state'], 'Collecting batch')
        self.assertEqual(summary['queued'], 3)
        self.assertIn('3/5 collected · 23s remaining', inspection_text('CENT', summary))

    def test_live_snapshot_reads_batch_deadline_and_freezes_on_pause(self):
        preparation = self.workflow.create_connected_node(self.analyzer.node_id, 'Preparation', 60,
            metadata={'node_type': 'specimens processing', 'required_staff': 1,
                      'human_throughput': 60, 'batch_size': 5, 'batch_wait_minutes': 1})
        live = LiveSimulationSession(self.workflow, count=3)
        live.engine._batch_queues[preparation.node_id] = [(item, None, 0) for item in live.engine.specimens]
        live.elapsed = 30 / 3600
        live.state = 'paused'
        first = live.snapshot()['batches'][preparation.node_id]
        self.assertEqual(first['collected'], 3)
        self.assertEqual(first['size'], 5)
        self.assertAlmostEqual(first['remaining_seconds'], 30)
        self.assertEqual(live.snapshot()['batches'][preparation.node_id], first)

    def test_inspector_order_selection_highlights_only_selected_order(self):
        inspector = EquipmentInspector.__new__(EquipmentInspector)
        inspector.orders = Mock()
        inspector.orders.curselection.return_value = (1,)
        inspector.order_ids = ['O1', 'O2']
        inspector.renderer = Mock()
        inspector.update = Mock()
        inspector.select_order()
        self.assertEqual(inspector.renderer.selected_order_id, 'O2')
        inspector.renderer.render_flow.assert_called_once()
        inspector.update.assert_called_once()

    def test_waiting_badge_shows_exact_count_without_a_rack_or_extra_tube(self):
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.workflow = self.workflow
        renderer.floor_plan = FloorPlan()
        renderer.floor_plan.equipment_instances.append(EquipmentInstance('CHEM', 'chemistry_analyzer', 2, 2))
        renderer.canvas = Mock()
        renderer.canvas.bbox.return_value = (100, 120, 356, 376)
        renderer._equipment_canvas_items = {'CHEM': 1}
        renderer.live_session = Mock()
        renderer.live_session.snapshot.return_value = self.snapshot
        renderer.render_equipment_status()
        renderer.canvas.create_image.assert_not_called()
        labels = [call.kwargs.get('text') for call in renderer.canvas.create_text.call_args_list]
        self.assertIn('Waiting: 3', labels)
        self.assertFalse(any('collected' in (label or '') for label in labels))
        renderer.canvas.tag_lower.assert_called_once()

    def test_processing_bar_disappears_when_operation_is_waiting(self):
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.workflow = self.workflow
        renderer.floor_plan = FloorPlan()
        renderer.floor_plan.equipment_instances.append(EquipmentInstance('CHEM', 'chemistry_analyzer', 2, 2))
        renderer.canvas = Mock()
        renderer.canvas.bbox.return_value = (100, 120, 356, 376)
        renderer._equipment_canvas_items = {'CHEM': 1}
        renderer.live_session = Mock()
        renderer.live_session.snapshot.return_value = self.snapshot
        job = self.snapshot['work_items'][0]
        job['progress'] = dict(fraction=.5, remaining_seconds=12, phase='Testing')
        renderer.render_equipment_status()
        labels = [call.kwargs.get('text') for call in renderer.canvas.create_text.call_args_list]
        self.assertIn('Test: 50% · 12s', labels)
        del job['progress']
        job['state'] = 'Waiting for staff'
        renderer.canvas.reset_mock()
        renderer.render_equipment_status()
        labels = [call.kwargs.get('text', '') for call in renderer.canvas.create_text.call_args_list]
        self.assertFalse(any('%' in label for label in labels))

    def test_snapshot_loading_and_unloading_follow_actual_analyzer_history(self):
        live = LiveSimulationSession(self.workflow, count=1)
        specimen = live.engine.specimens[0]
        event = dict(event='arrived', node_id=self.analyzer.node_id, specimen_id=specimen.specimen_id,
                     order_id='O1', time=0)
        live._activity(event)
        live._activity(dict(event, event='started'))
        specimen.record_event('Analyzer', 'human_started', time=0)
        self.assertEqual(live.snapshot()['work_items'][0]['state'], 'Loading')
        specimen.record_event('Analyzer', 'test_finished', test_code='GLU', time=.1)
        specimen.record_event('Analyzer', 'human_started', time=.1)
        work = live.snapshot()['work_items'][0]
        self.assertEqual(work['state'], 'Unloading')
        self.assertEqual(work['completed_tests'], ['GLU'])
        self.assertEqual(live.snapshot()['order_routes'], {'O1': [self.analyzer.node_id]})
