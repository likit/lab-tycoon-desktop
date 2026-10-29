"""Closing suspended analyzer processes must not yield during staff cleanup."""
import unittest
from app.system.model import Workflow
from app.system.model.specimen import Specimen
from app.system.simulation import WorkflowSimulationEngine, WorkflowSimulationSettings


class GeneratorCleanupTests(unittest.TestCase):
    def test_close_analyzer_while_holding_staff(self):
        for node_type, phase in (('manual analyzer', 'Testing'),
                                 ('automated analyzer', 'Staff work')):
            with self.subTest(node_type=node_type):
                workflow = Workflow()
                node = workflow.create_node('Analyzer', 20, metadata={
                    'node_type': node_type, 'throughput': 20,
                    'human_throughput': 60, 'required_staff': 1,
                    'analyzer_tests': ['TG']})
                workflow.create_connected_node(node.node_id, 'Reporting', 0)
                specimen = Specimen('demo', metadata={'pending_tests': ['TG'], 'all_tests': ['TG']})
                engine = WorkflowSimulationEngine(workflow, specimens=[specimen],
                    settings=WorkflowSimulationSettings(specimens_number=1, staff_capacity=1,
                        system_variation=0, human_variation=0))
                generator = engine._specimen_process(0)
                engine.env.process(generator)
                for _ in range(100):
                    if any(item['phase'] == phase for item in engine.active_processing.values()):
                        break
                    engine.env.step()
                else:
                    self.fail('Analyzer did not reach expected processing phase')
                self.assertEqual(engine.staff_pool.level, 0)
                generator.close()
                self.assertEqual(engine.staff_pool.level, 1)
                self.assertEqual(engine.active_processing, {})
                self.assertEqual(engine.completed_specimens, [])
                self.assertEqual(specimen.metadata['pending_tests'], ['TG'])
