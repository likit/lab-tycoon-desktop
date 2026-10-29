"""Tests for the reorganized Lab Tycoon main-window shell."""

from __future__ import annotations

import unittest

from app.main_ui import MainWindowContext, build_main_menu, update_context_elements, project_action_allowed


class MainWindowContextTests(unittest.TestCase):
    def test_default_context_represents_no_open_project(self) -> None:
        context = MainWindowContext()

        self.assertEqual(context.project_name, "No project open")
        self.assertEqual(context.laboratory_name, "")
        self.assertEqual(context.scenario_name, "Default")
        self.assertEqual(context.project_status, "Ready")


    def test_project_indicator_and_controls_follow_open_close_state_with_simulation_hidden(self):
        from unittest.mock import MagicMock, Mock
        from collections import defaultdict
        elements = defaultdict(Mock)
        window = MagicMock()
        window.__getitem__.side_effect = elements.__getitem__
        context = MainWindowContext(project_name='Teaching Lab', has_active_project=True, modified=True)
        update_context_elements(window, context, simulation_enabled=False)
        window['-ACTIVE-PROJECT-'].update.assert_called_with('Active project: Teaching Lab')
        window['-PROJECT-STATUS-'].update.assert_called_with('Unsaved changes')
        window['-ORDERS-'].update.assert_called_with(disabled=False)
        update_context_elements(window, MainWindowContext(), simulation_enabled=False)
        window['-ACTIVE-PROJECT-'].update.assert_called_with('No active project')
        window['-ORDERS-'].update.assert_called_with(disabled=True)
        window['-PROJECT-REQUIRED-'].update.assert_called_with(visible=True)

    def test_workspace_events_require_project_but_project_selection_remains_available(self):
        for event in ('Orders', '-ORDERS-', 'Tests', 'Analyze', 'Database Explorer',
                      'SQL Editor', 'Scenarios', 'Save', 'Close Project', 'Settings...'):
            self.assertFalse(project_action_allowed(event, False), event)
            self.assertTrue(project_action_allowed(event, True), event)
        for event in ('New Project...', 'Open Project...', 'Open Demo Project',
                      '-NEW-PROJECT-', '-OPEN-PROJECT-', 'Exit', 'Sign In', 'Program Settings...'):
            self.assertTrue(project_action_allowed(event, False), event)


class MainMenuTests(unittest.TestCase):
    def test_no_project_menu_disables_operational_actions(self):
        for enabled in (False, True):
            menu = dict(build_main_menu(simulation_enabled=enabled, has_active_project=False))
            self.assertIn('Open Demo Project', menu['File'])
            self.assertIn('!Save', menu['File'])
            self.assertIn('!Tests', menu['LIS'])
            self.assertIn('!Orders', menu['LIS'])
            self.assertIn('!Database Explorer', menu['Tools'])
            self.assertIn('Program Settings...', menu['File'])

    def test_menu_can_be_rendered_by_freesimplegui_with_simulation_on_or_off(self):
        from unittest.mock import Mock, patch
        import FreeSimpleGUI as sg
        for enabled in (True, False):
            with self.subTest(simulation_enabled=enabled):
                root = Mock()
                element = Mock(Tearoff=False, Font=None, BackgroundColor=None,
                               TextColor=None, DisabledTextColor=None, ItemFont=None)
                with patch('FreeSimpleGUI.elements.helpers.tk.Menu', side_effect=lambda *a, **k: Mock()):
                    for section in build_main_menu(simulation_enabled=enabled, has_active_project=False):
                        sg.AddMenuItem(root, section, element)
                labels = [call.kwargs['label'] for call in root.add_cascade.call_args_list]
                self.assertEqual('Simulation' in labels, enabled)
                self.assertIn('LIS', labels)

    def test_menu_uses_platform_functional_areas(self) -> None:
        menu = build_main_menu()

        self.assertEqual(
            [section[0] for section in menu],
            ["File", "LIS", "Simulation", "Analytics", "Tools", "User", "Help"],
        )

    def test_existing_design_and_tool_windows_have_active_entries(self) -> None:
        menu = dict(build_main_menu())

        self.assertIn("Settings...", menu["LIS"])
        items = menu['Simulation']
        simulation = {item: items[i + 1] for i, item in enumerate(items[:-1]) if isinstance(item, str) and isinstance(items[i + 1], list)}
        self.assertIn('Floor Plan', simulation['Laboratory Design'])
        self.assertIn('Add Workflow...', simulation['Workflows'])
        self.assertIn('Open Workflow...', simulation['Workflows'])
        self.assertEqual(menu["Tools"], ["Database Explorer", "Rule Preview", "SQL Editor"])

    def test_unimplemented_features_are_disabled(self) -> None:
        menu = dict(build_main_menu())

        self.assertIn("New Project...", menu["File"])
        self.assertIn("Open Project...", menu["File"])
        self.assertIn("Save", menu["File"])
        self.assertIn("Save As...", menu["File"])
        self.assertIn("Close Project", menu["File"])
        self.assertIn("!Run Simulation", menu["Simulation"])
        self.assertIn("!Operational Analytics", menu["Analytics"])
        self.assertIn(["Floor Plan", "!SOPs", "!Equipment & Resources"], menu["Simulation"])

    def test_simulation_can_be_hidden_without_hiding_program_settings_or_lis(self):
        menu = dict(build_main_menu(simulation_enabled=False))
        self.assertNotIn('Simulation', menu)
        self.assertNotIn('Laboratory Design', menu)
        self.assertNotIn('Workflow', menu)
        self.assertIn('LIS', menu)
        self.assertIn('Program Settings...', menu['File'])

    def test_user_menu_exposes_current_account_actions(self) -> None:
        menu = dict(build_main_menu())

        self.assertEqual(menu["User"], ["Profile", "Sign In", "Sign Out"])


if __name__ == "__main__":
    unittest.main()
