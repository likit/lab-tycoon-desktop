"""Shared arrival distributions and engine integration."""
import unittest
from app.demo_project import create_demo_project
from app.system.arrivals import arrival_offsets
from app.system.live_simulation import LiveSimulationSession
from app.system.scenario_editor import configure_scenario


class ArrivalTests(unittest.TestCase):
    def test_legacy_fixed_and_simultaneous_arrivals(self):
        self.assertEqual(arrival_offsets(4, .5), [0, .5, 1, 1.5])
        self.assertEqual(arrival_offsets(3, 0), [0, 0, 0])

    def test_exponential_is_seeded_positive_and_has_expected_mean(self):
        times = arrival_offsets(10001, .05, 'exponential', 42)
        self.assertEqual(times, arrival_offsets(10001, .05, 'exponential', 42))
        gaps = [b-a for a, b in zip(times, times[1:])]
        self.assertTrue(all(gap > 0 for gap in gaps))
        self.assertGreater(len(set(round(gap, 6) for gap in gaps)), 100)
        self.assertAlmostEqual(sum(gaps) / len(gaps), .05, delta=.002)

    def test_invalid_distribution_parameters(self):
        for args in ((0, 'exponential'), (-1, 'fixed'), (float('nan'), 'fixed'),
                     (1, 'unknown'), (1, 'fixed', 'bad'), (1, 'fixed', 1.5)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                arrival_offsets(3, *args)

    def test_scenario_engine_uses_shared_schedule_and_preserves_settings(self):
        project = create_demo_project()
        scenario = configure_scenario(project.workflow, source=project.workflow.active_scenario,
            name='Random arrivals', staff=1, arrival_minutes=3,
            arrival_distribution='exponential', arrival_seed='42')
        live = LiveSimulationSession(project.workflow, scenario=scenario, count=10,
            equipment_types={e.instance_id: e.equipment_type for e in project.floor.equipment_instances})
        live.engine.run()
        origin = live.generated_orders[0].order_datetime
        times = [(order.order_datetime-origin).total_seconds() / 3600 for order in live.generated_orders]
        for actual, expected in zip(times, arrival_offsets(10, .05, 'exponential', 42)):
            self.assertAlmostEqual(actual, expected, places=8)
        self.assertEqual(len(live.engine.completed_specimens), 10)
        duplicate = configure_scenario(project.workflow, source=scenario, duplicate=True,
            name='Copy', staff=1, arrival_minutes=3)
        self.assertEqual(duplicate.parameters['arrival_distribution'], 'exponential')
        self.assertEqual(duplicate.parameters['arrival_seed'], 42)
        before = scenario.to_dict()
        with self.assertRaises(ValueError):
            configure_scenario(project.workflow, source=scenario, name=scenario.name,
                staff=1, arrival_minutes=0, arrival_distribution='exponential')
        self.assertEqual(scenario.to_dict(), before)
