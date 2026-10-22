"""Floor entry points require a signed-in, active database account."""
import unittest
from unittest.mock import Mock, patch

from app.auth.windows import login_required
from app.project import LabTycoonProject
from app.project_workspace import open_project_lab
from app.system.floor_view import create_floor_view_window


class FloorAuthenticationTests(unittest.TestCase):
    def test_signed_out_entry_points_do_not_open_windows_or_hide_home(self):
        project, home = LabTycoonProject.create('Demo'), Mock()
        with patch('app.auth.windows.session_manager.is_logged_in', return_value=False), \
             patch('app.auth.windows.Session') as database, \
             patch('app.auth.windows.sg.popup_error') as denied, \
             patch('app.system.floor_view.sg.Window') as window, \
             patch('app.project_workspace.create_floor_view_window') as floor:
            open_project_lab(project, home_window=home)
            create_floor_view_window(project)
        database.assert_not_called()
        window.assert_not_called()
        floor.assert_not_called()
        home.hide.assert_not_called()
        self.assertEqual(denied.call_count, 2)

    def test_missing_and_inactive_accounts_cannot_enter_floor(self):
        for account in (None, Mock(active=False)):
            with self.subTest(account=account), \
                 patch('app.auth.windows.session_manager.is_logged_in', return_value=True), \
                 patch('app.auth.windows.Session') as database, \
                 patch('app.auth.windows.sg.popup_error') as denied, \
                 patch('app.system.floor_view.sg.Window') as window:
                database.return_value.__enter__.return_value.scalar.return_value = account
                create_floor_view_window(LabTycoonProject.create('Demo'))
                denied.assert_called_once()
                window.assert_not_called()

    def test_active_account_can_execute_protected_action(self):
        action = Mock(return_value='opened')
        with patch('app.auth.windows.session_manager.is_logged_in', return_value=True), \
             patch('app.auth.windows.Session') as database, \
             patch('app.auth.windows.sg.popup_error') as denied:
            database.return_value.__enter__.return_value.scalar.return_value = Mock(active=True)
            self.assertEqual(login_required(action)('project', main_workspace=True), 'opened')
        action.assert_called_once_with('project', main_workspace=True)
        denied.assert_not_called()
