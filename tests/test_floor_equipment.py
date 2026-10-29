"""Floor equipment editing updates the project workflow through existing forms."""
import unittest
from unittest.mock import patch, Mock
from app.demo_project import create_demo_project
from app.system.floor_equipment import edit_floor_equipment


class FloorEquipmentTests(unittest.TestCase):
    def test_selected_analyzer_settings_update_and_mark_project_dirty(self):
        project = create_demo_project()
        project.is_dirty = False
        settings = dict(equipment_id='CHEM-001', throughput=40, human_throughput=60,
                        capacity=2, required_staff=1, description='Updated', enabled=True)
        with patch('app.system.floor_equipment._show_equipment_node_dialog', return_value=settings):
            self.assertTrue(edit_floor_equipment(project, project.workflow, 'CHEM-001'))
        node = next(n for n in project.workflow.graph.iter_nodes() if n.metadata.get('equipment_id') == 'CHEM-001')
        self.assertEqual(node.widget.throughput, 40)
        self.assertEqual(node.widget.capacity, 2)
        self.assertTrue(project.is_dirty)

    def test_cancel_leaves_equipment_unchanged(self):
        project = create_demo_project()
        before = project.workflow.to_dict()
        with patch('app.system.floor_equipment._show_equipment_node_dialog', return_value=None):
            self.assertFalse(edit_floor_equipment(project, project.workflow, 'CENT-001'))
        self.assertEqual(project.workflow.to_dict(), before)

    def test_shared_station_prompts_for_step_before_editing(self):
        project = create_demo_project()
        window = Mock()
        window.read.return_value = ('Cancel', {})
        with patch('app.system.floor_equipment.create_window', return_value=window), \
             patch('app.system.floor_equipment._show_equipment_node_dialog') as edit:
            self.assertFalse(edit_floor_equipment(project, project.workflow, 'REPORT-001'))
            edit.assert_not_called()
            window.close.assert_called_once()
