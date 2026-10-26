"""Source choices survive restarts without generating another batch."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.order_source_preferences import remember_order_source, restore_order_source
from app.system.order_sources import (SQLiteOrderSource, get_active_order_source,
                                      set_active_order_source, InMemoryOrderSource)
from app.system.model import Workflow
from app.system.storage import WorkflowDocument
from app.system.lis_analysis import AnalysisWorkflowChoice
from app.system.simulation_lis import ScenarioLISOrderSource


class OrderSourcePreferenceTests(unittest.TestCase):
    def setUp(self):
        old = get_active_order_source()
        self.addCleanup(set_active_order_source, old)

    def test_simulation_choice_roundtrip_restores_scenario_without_open_project(self):
        workflow = Workflow()
        document = WorkflowDocument('wf', 'Teaching', workflow)
        choice = AnalysisWorkflowChoice('project:old:wf', 'Teaching', document, 'old')
        source = ScenarioLISOrderSource(choice, workflow.active_scenario.scenario_id)
        with patch('app.order_source_preferences.update_config_yaml') as save:
            remember_order_source(source)
        persisted = save.call_args.kwargs
        set_active_order_source(SQLiteOrderSource())
        with patch('app.order_source_preferences.config_dict', persisted), \
             patch('app.system.lis_analysis.analysis_workflow_choices', return_value=[]):
            self.assertIsNone(restore_order_source())
        restored = get_active_order_source()
        self.assertIsInstance(restored, ScenarioLISOrderSource)
        self.assertEqual(restored.scenario_id, source.scenario_id)
        self.assertEqual(restored.choice.key, choice.key)
        self.assertEqual(restored.choice.document.workflow.to_dict(), workflow.to_dict())
        self.assertIsNone(restored.batch_id)

    def test_restore_uses_current_workflow_instead_of_stale_snapshot(self):
        workflow = Workflow()
        choice = AnalysisWorkflowChoice('saved:wf', 'Teaching', WorkflowDocument('wf', 'Teaching', workflow))
        source = ScenarioLISOrderSource(choice, workflow.active_scenario.scenario_id)
        with patch('app.order_source_preferences.update_config_yaml') as save:
            remember_order_source(source)
        workflow.active_scenario.name = 'Updated'
        with patch('app.order_source_preferences.config_dict', save.call_args.kwargs), \
             patch('app.system.lis_analysis.analysis_workflow_choices', return_value=[choice]):
            restore_order_source()
        self.assertEqual(get_active_order_source().scenario.name, 'Updated')

    def test_sqlite_choice_is_saved_and_restored(self):
        with patch('app.order_source_preferences.update_config_yaml') as save:
            remember_order_source(SQLiteOrderSource())
        save.assert_called_once_with(order_source_selection={'kind': 'sqlite'})
        with patch('app.order_source_preferences.config_dict', save.call_args.kwargs):
            self.assertIsNone(restore_order_source())
        self.assertIsInstance(get_active_order_source(), SQLiteOrderSource)

    def test_uploaded_selection_reloads_the_saved_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'orders.csv'
            path.write_text('order_id,patient_id,test_code,order_time,priority\nO1,P1,GLU,2026-01-01 09:00:00,routine\n')
            with patch('app.order_source_preferences.config_dict',
                       {'order_source_selection': {'kind': 'uploaded', 'path': str(path)}}):
                self.assertIsNone(restore_order_source())
            source = get_active_order_source()
            self.assertIsInstance(source, InMemoryOrderSource)
            self.assertEqual(source.count(), 1)

    def test_missing_uploaded_file_falls_back_without_overwriting_saved_selection(self):
        preference = {'order_source_selection': {'kind': 'uploaded', 'path': '/nonexistent/orders.csv'}}
        with patch('app.order_source_preferences.config_dict', preference), \
             patch('app.order_source_preferences.update_config_yaml') as save:
            self.assertIn('using SQLite', restore_order_source())
        save.assert_not_called()
        self.assertIsInstance(get_active_order_source(), SQLiteOrderSource)
