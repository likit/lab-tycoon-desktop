"""Process-specific staff artwork selection and shared workstations."""
import unittest
from app.system.model import Workflow
from app.system.staff_activity import staff_activity
from app.system.live_simulation import LiveSimulationSession


class StaffActivityTests(unittest.TestCase):
    def test_explicit_process_type_selects_pose_independent_of_station(self):
        workflow = Workflow()
        for node_type, role in [('specimen registration', 'registration'),
                                ('specimens processing', 'processing'),
                                ('result reporting', 'reporting'),
                                ('result validating', 'validating')]:
            node = workflow.create_node('Other task', metadata={'node_type': node_type})
            self.assertEqual(staff_activity(node, 'reporting_station'), role)

    def test_legacy_names_still_select_reporting_and_validating(self):
        workflow = Workflow()
        self.assertEqual(staff_activity(workflow.create_node('Reporting')), 'reporting')
        self.assertEqual(staff_activity(workflow.create_node('Validating')), 'validating')
        self.assertEqual(staff_activity(workflow.create_node('Registration')), 'registration')

    def test_reporting_and_validating_share_location_with_distinct_work(self):
        workflow = Workflow()
        reporting = workflow.create_node('Reporting', metadata={'node_type': 'result reporting'})
        validating = workflow.create_connected_node(reporting.node_id, 'Validating', metadata={'node_type': 'result validating'})
        live = LiveSimulationSession(workflow, equipment_types={'REPORT-001': 'reporting_station'})
        for node in (reporting, validating):
            live._activity(dict(event='staff_acquired', node_id=node.node_id, specimen_id='order', staff_count=1, time=0))
        allocations = live.snapshot()['staff_assignments']
        self.assertEqual({a['equipment_id'] for a in allocations}, {'REPORT-001'})
        self.assertEqual({a['activity'] for a in allocations}, {'reporting', 'validating'})
