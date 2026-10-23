"""Named eligibility, atomic teams and scenario persistence."""
import unittest
from unittest.mock import Mock, patch
import simpy

from app.system.model import Workflow
from app.system.scenario_editor import configure_scenario, allowed_staff_dialog
from app.system.simulation import WorkflowSimulationEngine, WorkflowSimulationSettings
from app.system.live_simulation import LiveSimulationSession
from app.system.workforce import WorkforcePool, eligible_staff


class WorkforceTests(unittest.TestCase):
    def build(self, *, node_type='specimens processing', required=1, batch=1):
        workflow = Workflow()
        source = workflow.create_node('Orders', kind='order_adapter',
            metadata={'node_type': 'order adapter', 'adapter_type': 'simulation'})
        node = workflow.create_connected_node(source.node_id, 'Work', 0,
            metadata={'node_type': node_type, 'human_throughput': 60,
                      'required_staff': required, 'capacity': 3, 'batch_size': batch,
                      'batch_wait_minutes': 0})
        scenario = configure_scenario(workflow, name='Named', staff=3, arrival_minutes=0,
            workforce=[dict(id='a', name='Alice'), dict(id='b', name='Bob'), dict(id='c', name='Carol')],
            system_variation=0, human_variation=0)
        return workflow, node, scenario

    def engine(self, workflow, scenario, count=3):
        events = []
        settings = WorkflowSimulationSettings.from_scenario(specimens_number=count,
            workflow=workflow, scenario=scenario)
        return WorkflowSimulationEngine(workflow, settings=settings, scenario=scenario,
                                        on_activity=events.append), events

    def test_named_eligibility_serializes_work_despite_free_other_staff(self):
        workflow, node, scenario = self.build()
        scenario.staff_assignments[node.node_id] = ['b']
        engine, events = self.engine(workflow, scenario)
        result = engine.run()
        self.assertAlmostEqual(result.makespan * 60, 3)
        acquired = [e for e in events if e['event'] == 'staff_acquired']
        self.assertEqual([e['staff_names'] for e in acquired], [['Bob']] * 3)
        self.assertEqual(engine.staff_pool.level, 3)

    def test_all_and_two_person_teams(self):
        workflow, node, scenario = self.build(required=2)
        engine, events = self.engine(workflow, scenario, count=2)
        result = engine.run()
        self.assertAlmostEqual(result.makespan * 60, 2)
        busy = set()
        for event in events:
            if event['event'] == 'staff_acquired':
                self.assertEqual(len(event['staff_ids']), 2)
                self.assertFalse(busy.intersection(event['staff_ids']))
                busy.update(event['staff_ids'])
            elif event['event'] == 'staff_released':
                busy.difference_update(event['staff_ids'])
        self.assertFalse(busy)

    def test_same_person_cannot_overlap_across_different_nodes(self):
        workflow, first, scenario = self.build()
        second = workflow.create_connected_node(first.node_id, 'Second station', 0,
            metadata={'node_type': 'result reporting', 'human_throughput': 60,
                      'required_staff': 1, 'capacity': 3})
        scenario.staff_assignments = {first.node_id: ['a'], second.node_id: ['a']}
        engine, events = self.engine(workflow, scenario)
        result = engine.run()
        self.assertAlmostEqual(result.makespan * 60, 6)
        occupied = set()
        visited = set()
        for event in events:
            if event['event'] == 'staff_acquired':
                self.assertFalse(occupied.intersection(event['staff_ids']))
                occupied.update(event['staff_ids'])
                visited.add(event['node_id'])
            elif event['event'] == 'staff_released':
                occupied.difference_update(event['staff_ids'])
        self.assertEqual(visited, {first.node_id, second.node_id})

    def test_automated_analyzer_human_phase_uses_selected_person(self):
        workflow, node, scenario = self.build(node_type='automated analyzer')
        node.widget.throughput = 60
        scenario.staff_assignments[node.node_id] = ['b']
        engine, events = self.engine(workflow, scenario)
        engine.run()
        acquired = [e for e in events if e['event'] == 'staff_acquired']
        self.assertEqual(len(acquired), 3)
        self.assertTrue(all(e['staff_names'] == ['Bob'] for e in acquired))

    def test_positive_required_without_human_rate_reserves_during_processing(self):
        workflow, node, scenario = self.build()
        node.widget.human_throughput = 0
        node.widget.throughput = 60
        scenario.staff_assignments[node.node_id] = ['a']
        engine, events = self.engine(workflow, scenario)
        result = engine.run()
        self.assertAlmostEqual(result.makespan * 60, 3)
        self.assertAlmostEqual(result.node_stats[node.node_id].total_staff_busy_time * 60, 3)
        self.assertEqual(sum(e['event'] == 'staff_acquired' for e in events), 3)

    def test_atomic_allocation_does_not_hold_partial_teams_or_block_disjoint_work(self):
        env = simpy.Environment()
        roster = [dict(id='a', name='Alice'), dict(id='b', name='Bob'), dict(id='c', name='Carol')]
        pool = WorkforcePool(env, roster)
        first = pool.get(1, roster[:1])
        group = pool.get(2, roster[:2])
        other = pool.get(1, roster[1:])
        self.assertFalse(group.triggered)
        self.assertTrue(other.triggered)
        self.assertEqual(other.value[0]['id'], 'b')
        pool.put(first.value)
        self.assertFalse(group.triggered)
        pool.put(other.value)
        self.assertTrue(group.triggered)
        self.assertEqual({m['id'] for m in group.value}, {'a', 'b'})

    def test_roundtrip_duplicate_and_dynamic_all(self):
        workflow, node, scenario = self.build()
        scenario.staff_assignments[node.node_id] = ['a', 'c']
        restored = Workflow.from_dict(workflow.to_dict()).scenarios[-1]
        self.assertEqual(restored.staff_assignments, scenario.staff_assignments)
        copy = configure_scenario(workflow, name='Copy', source=scenario, duplicate=True,
                                  staff=3, arrival_minutes=0)
        copy.staff_assignments[node.node_id].append('b')
        self.assertEqual(scenario.staff_assignments[node.node_id], ['a', 'c'])
        self.assertEqual(len(eligible_staff(scenario.workforce, {}, node.node_id)), 3)
        scenario.workforce.append(dict(id='d', name='Dan'))
        self.assertEqual(len(eligible_staff(scenario.workforce, {}, node.node_id)), 4)

    def test_invalid_assignments_fail_before_run(self):
        workflow, node, scenario = self.build(required=2)
        for ids in ([], ['a'], ['removed']):
            scenario.staff_assignments[node.node_id] = ids
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.engine(workflow, scenario)

    def test_zero_required_is_unattended_even_with_no_eligible_staff(self):
        workflow, node, scenario = self.build(required=0)
        scenario.staff_assignments[node.node_id] = []
        engine, events = self.engine(workflow, scenario)
        engine.run()
        self.assertFalse(any(e['event'] == 'staff_acquired' for e in events))

    def test_batch_and_manual_analyzer_use_named_staff(self):
        for kind, batch in (('specimens processing', 3), ('manual analyzer', 1)):
            workflow, node, scenario = self.build(node_type=kind, batch=batch)
            scenario.staff_assignments[node.node_id] = ['c']
            engine, events = self.engine(workflow, scenario)
            engine.run()
            acquired = [e for e in events if e['event'] == 'staff_acquired']
            self.assertTrue(acquired)
            self.assertTrue(all(e['staff_names'] == ['Carol'] for e in acquired))
            if batch == 3:
                self.assertEqual(len(acquired), 1)
                self.assertEqual(acquired[0]['batch_size'], 3)

    def test_live_waiting_counts_do_not_advance_completed_counts(self):
        workflow, node, scenario = self.build()
        scenario.staff_assignments[node.node_id] = ['a']
        live = LiveSimulationSession(workflow, scenario=scenario, count=3)
        live.engine.env.process(live.engine._arrival_process())
        live.engine.env.run(until=.005)
        snapshot = live.snapshot()
        self.assertEqual(len(snapshot['staff_assignments']), 1)
        self.assertEqual(snapshot['staff_assignments'][0]['staff_names'], ['Alice'])
        self.assertEqual(len(snapshot['staff_waiting']), 2)
        self.assertEqual(snapshot['counts'][node.node_id]['finished'], 0)
        live.engine.env.run()
        self.assertFalse(live.snapshot()['staff_waiting'])
        self.assertEqual(live.snapshot()['counts'][node.node_id]['finished'], 3)

    def test_allowed_staff_dialog_saves_names_as_ids(self):
        workflow, node, scenario = self.build()
        window = Mock()
        window.read.return_value = ('Save', {'all': False, 'names': ['Bob', 'Carol']})
        with patch('app.system.scenario_editor.create_window', return_value=window):
            self.assertEqual(allowed_staff_dialog(node, scenario.workforce), (True, ['b', 'c']))
        window.close.assert_called_once()
