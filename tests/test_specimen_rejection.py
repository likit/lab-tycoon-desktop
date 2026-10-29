"""Scenario rejection decisions stop specimens before testing."""
import unittest
from app.demo_project import create_demo_project
from app.system.live_simulation import LiveSimulationSession
from app.system.scenario_editor import configure_scenario


class SpecimenRejectionTests(unittest.TestCase):
    def run_demo(self, probability, seed=42):
        project = create_demo_project()
        project.workflow.active_scenario.parameters['rejection_probability'] = probability
        live = LiveSimulationSession(project.workflow, count=20, seed=seed,
            equipment_types={e.instance_id: e.equipment_type for e in project.floor.equipment_instances})
        result = live.engine.run()
        return live, result

    def test_zero_and_one_hundred_percent(self):
        for probability in (0, 1):
            with self.subTest(probability=probability):
                live, result = self.run_demo(probability)
                self.assertEqual(len(result.rejected_specimens), 20 * probability)
                self.assertEqual(len(result.completed_specimens), 20 * (1-probability))
                self.assertEqual(live.snapshot()['orders_rejected'], 20 * probability)
                for specimen in result.rejected_specimens:
                    self.assertFalse(any(e['event'] == 'test_started' for e in specimen.history))
                    self.assertIn('TG', specimen.metadata['pending_tests'])
                    self.assertTrue(specimen.payload.metadata['rejected'])

    def test_seeded_mixed_outcomes_and_scenario_validation(self):
        _, first = self.run_demo(.5)
        _, second = self.run_demo(.5)
        rejected = [s.specimen_id for s in first.rejected_specimens]
        self.assertEqual(rejected, [s.specimen_id for s in second.rejected_specimens])
        self.assertGreater(len(rejected), 0)
        self.assertLess(len(rejected), 20)
        project = create_demo_project()
        scenario = configure_scenario(project.workflow, source=project.workflow.active_scenario,
            name='Quality', staff=1, arrival_minutes=1, rejection_percent=5)
        self.assertEqual(scenario.parameters['rejection_probability'], .05)
        before = scenario.to_dict()
        with self.assertRaises(ValueError):
            configure_scenario(project.workflow, source=scenario, name='Quality', staff=1,
                               arrival_minutes=1, rejection_percent=101)
        self.assertEqual(scenario.to_dict(), before)
