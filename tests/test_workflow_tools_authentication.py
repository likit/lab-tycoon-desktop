"""Authentication applies at every public workflow and Tools UI entry point."""
import unittest
from unittest.mock import Mock, patch

from app.project import LabTycoonProject
from app.project_workspace import add_project_workflow, choose_project_workflow, open_project_workflow
from app.system.workflow_editor import create_workflow_window, open_workflow_editor_window
from app.system.windows import (
    create_workflow_window as create_legacy_workflow_window,
    create_rule_preview_window,
    create_sql_window,
)
from app.database_explorer import create_database_explorer_window


class WorkflowToolsAuthenticationTests(unittest.TestCase):
    def test_all_entry_points_deny_signed_out_missing_and_inactive_accounts(self):
        project = LabTycoonProject.create('Demo')
        entries = [(add_project_workflow, (project,)),
                   (choose_project_workflow, (project,)),
                   (open_project_workflow, (project,)),
                   (create_workflow_window, ()),
                   (open_workflow_editor_window, ()),
                   (create_legacy_workflow_window, ()),
                   (create_database_explorer_window, ()),
                   (create_rule_preview_window, ()),
                   (create_sql_window, ())]
        for logged_in, account in ((False, None), (True, None), (True, Mock(active=False))):
            for entry, args in entries:
                with self.subTest(entry=entry.__module__ + '.' + entry.__name__, logged_in=logged_in, account=account), \
                     patch('app.auth.windows.session_manager.is_logged_in', return_value=logged_in), \
                     patch('app.auth.windows.Session') as database, \
                     patch('app.auth.windows.sg.popup_error') as denied, \
                     patch('FreeSimpleGUI.Window') as window, \
                     patch('FreeSimpleGUI.popup_get_text') as prompt:
                    database.return_value.__enter__.return_value.scalar.return_value = account
                    self.assertIsNone(entry(*args))
                    denied.assert_called_once()
                    window.assert_not_called()
                    prompt.assert_not_called()
                    if not logged_in:
                        database.assert_not_called()
        self.assertEqual(len(project.workflows), 1)

    def test_active_account_can_add_workflow(self):
        project = LabTycoonProject.create('Demo')
        with patch('app.auth.windows.session_manager.is_logged_in', return_value=True), \
             patch('app.auth.windows.Session') as database, \
             patch('FreeSimpleGUI.popup_get_text', return_value='Authorized workflow'):
            database.return_value.__enter__.return_value.scalar.return_value = Mock(active=True)
            workflow_id = add_project_workflow(project)
        self.assertEqual(project.get_workflow(workflow_id).name, 'Authorized workflow')
