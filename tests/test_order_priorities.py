"""Configurable STAT mix preserves arrival schedules and legacy defaults."""
import unittest
from app.demo_project import create_demo_project
from app.system.order_priorities import generated_priorities
from app.system.live_simulation import LiveSimulationSession
from app.system.scenario_editor import configure_scenario


class OrderPriorityTests(unittest.TestCase):
    def test_boundaries_validation_and_seed(self):
        self.assertEqual(generated_priorities(3), ['routine'] * 3)
        self.assertEqual(generated_priorities(3, 1), ['urgent'] * 3)
        priorities = generated_priorities(1000, .2, 42)
        self.assertEqual(priorities, generated_priorities(1000, .2, 42))
        self.assertGreater(priorities.count('urgent'), 140)
        self.assertLess(priorities.count('urgent'), 260)
        for invalid in (-.1, 1.1, float('nan')):
            with self.assertRaises(ValueError):
                generated_priorities(3, invalid)

    def test_floor_mix_does_not_change_arrival_times(self):
        project = create_demo_project()
        scenario = project.workflow.active_scenario
        scenario.parameters.update(arrival_distribution='exponential', interarrival_time=.05)
        schedules = []
        for percent in (0, 100):
            configure_scenario(project.workflow, source=scenario, name=scenario.name,
                staff=1, arrival_minutes=3, stat_percent=percent)
            live = LiveSimulationSession(project.workflow, count=10, seed=42,
                equipment_types={e.instance_id:e.equipment_type for e in project.floor.equipment_instances})
            live.engine.run()
            self.assertEqual({o.priority for o in live.generated_orders}, {'urgent' if percent else 'routine'})
            schedules.append([o.order_datetime for o in live.generated_orders])
            self.assertEqual(len(live.engine.completed_specimens), 10)
        self.assertEqual(*schedules)
        before = scenario.to_dict()
        with self.assertRaises(ValueError):
            configure_scenario(project.workflow, source=scenario, name=scenario.name,
                staff=1, arrival_minutes=3, stat_percent=101)
        self.assertEqual(scenario.to_dict(), before)
        duplicate = configure_scenario(project.workflow, source=scenario, duplicate=True,
            name='Copy', staff=1, arrival_minutes=3)
        self.assertEqual(duplicate.parameters['stat_probability'], 1)
