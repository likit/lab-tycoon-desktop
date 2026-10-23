"""Tests for application-wide UI style conventions."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from app.ui_styles import DANGER_BUTTON_COLOR


class DangerButtonStyleTests(unittest.TestCase):
    def test_danger_button_color_is_white_on_red(self) -> None:
        self.assertEqual(DANGER_BUTTON_COLOR, ("white", "#dc2626"))

    def test_all_close_and_exit_buttons_define_button_color(self) -> None:
        app_dir = Path(__file__).resolve().parents[1] / "app"
        missing_style: list[str] = []

        for path in app_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                is_close_or_exit = node.func.attr in {"CloseButton", "Exit"}
                if node.func.attr == "Button" and node.args:
                    label = node.args[0]
                    is_close_or_exit = isinstance(label, ast.Constant) and label.value in {"Close", "Exit"}
                if not is_close_or_exit:
                    continue
                if not any(keyword.arg == "button_color" for keyword in node.keywords):
                    missing_style.append(f"{path.relative_to(app_dir.parent)}:{node.lineno}")

        self.assertEqual(missing_style, [])


if __name__ == "__main__":
    unittest.main()


class WindowActivationTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.registry = patch('app.ui_styles._windows', [])
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def window(self):
        from unittest.mock import Mock
        window = Mock()
        window.TKroot.winfo_exists.return_value = True
        window.TKroot.winfo_viewable.return_value = True
        window.TKroot.focus_get.return_value = None
        return window

    def test_child_has_owner_without_permanent_topmost_and_restores_focus(self):
        from unittest.mock import patch, Mock
        from app.ui_styles import create_window
        parent, child = self.window(), self.window()
        with patch('app.ui_styles.sg.Window', side_effect=[parent, child]) as constructor:
            create_window('Parent')
            create_window('Child', modal=True, keep_on_top=True)
        self.assertFalse(constructor.call_args.kwargs['keep_on_top'])
        self.assertTrue(constructor.call_args.kwargs['finalize'])
        child.TKroot.transient.assert_called_once_with(parent.TKroot)
        child.TKroot.lift.assert_called_once()
        callback = child.TKroot.bind.call_args.args[1]
        parent.TKroot.after_idle.reset_mock()
        callback(Mock(widget=object()))
        parent.TKroot.after_idle.assert_not_called()
        callback(Mock(widget=child.TKroot))
        restore = parent.TKroot.after_idle.call_args.args[0]
        with patch('app.ui_styles.bring_window_to_front') as activate:
            restore()
            activate.assert_called_once_with(parent)

    def test_hidden_parent_is_not_used(self):
        from unittest.mock import patch
        from app.ui_styles import create_window
        parent, child = self.window(), self.window()
        with patch('app.ui_styles.sg.Window', side_effect=[parent, child]):
            create_window('Home')
            parent.TKroot.winfo_viewable.return_value = False
            create_window('Lab')
        child.TKroot.transient.assert_not_called()

    def test_always_on_top_is_explicit_and_inherited_by_child_dialogs(self):
        from unittest.mock import patch
        from app.ui_styles import create_window
        floor, scenarios, settings = [self.window() for _ in range(3)]
        with patch('app.ui_styles.sg.Window', side_effect=[floor, scenarios, settings]) as constructor:
            create_window('Floor Plan')
            create_window('Scenarios', parent=floor, always_on_top=True)
            create_window('Scenario Settings', parent=scenarios)
        self.assertFalse(constructor.call_args_list[0].kwargs['keep_on_top'])
        self.assertTrue(constructor.call_args_list[1].kwargs['keep_on_top'])
        self.assertTrue(constructor.call_args_list[2].kwargs['keep_on_top'])

    def test_floor_is_independent_and_not_topmost_even_after_topmost_dialog(self):
        from unittest.mock import patch
        from app.ui_styles import create_window
        dialog, floor, scenarios = [self.window() for _ in range(3)]
        with patch('app.ui_styles.sg.Window', side_effect=[dialog, floor, scenarios]) as constructor:
            create_window('Chooser', always_on_top=True)
            create_window('Floor Plan', window_role='floor', independent=True)
            create_window('Scenarios', parent=floor, always_on_top=True)
        self.assertFalse(constructor.call_args_list[1].kwargs['keep_on_top'])
        floor.TKroot.transient.assert_not_called()
        floor.TKroot.wm_attributes.assert_called_once_with('-topmost', 0)
        scenarios.TKroot.transient.assert_called_once_with(floor.TKroot)
        scenarios.TKroot.wm_attributes.assert_called_once_with('-topmost', 1)

    def test_closed_chooser_cannot_raise_parent_while_next_dialog_is_finalizing(self):
        from unittest.mock import patch, Mock
        from app.ui_styles import create_window
        parent, chooser, scenarios = self.window(), self.window(), self.window()
        with patch('app.ui_styles.sg.Window', side_effect=[parent, chooser]):
            create_window('Home')
            create_window('Choose workflow', parent=parent)
        on_destroy = chooser.TKroot.bind.call_args.args[1]
        on_destroy(Mock(widget=chooser.TKroot))
        restore = parent.TKroot.after_idle.call_args.args[0]
        chooser.TKroot.winfo_viewable.return_value = False

        def finalize(*args, **kwargs):
            restore()  # Tk processes the old callback while mapping Scenarios.
            return scenarios

        with patch('app.ui_styles.sg.Window', side_effect=finalize), \
             patch('app.ui_styles.bring_window_to_front') as activate:
            create_window('Scenarios', parent=parent)
        activate.assert_called_once_with(scenarios)
        scenarios.TKroot.transient.assert_called_once_with(parent.TKroot)

    def test_old_idle_activation_does_not_steal_focus_from_new_dialog(self):
        from unittest.mock import patch
        from app.ui_styles import create_window
        parent, child = self.window(), self.window()
        with patch('app.ui_styles.sg.Window', side_effect=[parent, child]):
            create_window('Home')
            activate_parent = parent.TKroot.after_idle.call_args.args[0]
            create_window('Scenarios', parent=parent)
        parent.TKroot.focus_force.reset_mock()
        activate_parent()
        parent.TKroot.focus_force.assert_not_called()

    def test_scenario_menu_keeps_floor_as_owner_after_workflow_chooser_closes(self):
        from unittest.mock import patch
        from app.ui_styles import create_window, active_window
        home, floor, chooser, scenarios = [self.window() for _ in range(4)]
        with patch('app.ui_styles.sg.Window', side_effect=[home, floor, chooser, scenarios]):
            create_window('Home')
            home.TKroot.winfo_viewable.return_value = False
            create_window('Floor Plan')
            menu_owner = active_window()
            self.assertIs(menu_owner, floor)
            create_window('Project Workflows', parent=menu_owner, modal=True)
            chooser.TKroot = None
            create_window('Scenarios', parent=menu_owner, modal=True)
        scenarios.TKroot.transient.assert_called_once_with(floor.TKroot)

    def test_hidden_explicit_owner_falls_back_to_visible_floor(self):
        from unittest.mock import patch
        from app.ui_styles import create_window
        home, floor, scenarios = [self.window() for _ in range(3)]
        with patch('app.ui_styles.sg.Window', side_effect=[home, floor, scenarios]):
            create_window('Home')
            home.TKroot.winfo_viewable.return_value = False
            create_window('Floor Plan')
            create_window('Scenarios', parent=home, modal=True)
        scenarios.TKroot.transient.assert_called_once_with(floor.TKroot)

    def test_floor_owner_can_be_selected_even_when_menu_focus_is_on_main(self):
        from unittest.mock import patch, Mock
        from app.ui_styles import create_window, active_window
        home, floor, scenarios = [self.window() for _ in range(3)]
        with patch('app.ui_styles.sg.Window', side_effect=[home, floor, scenarios]):
            create_window('Home')
            create_window('Floor Plan', window_role='floor')
            focus = Mock()
            focus.winfo_toplevel.return_value = home.TKroot
            home.TKroot.focus_get.return_value = focus
            self.assertIs(active_window(), home)
            self.assertIs(active_window(role='floor'), floor)
            create_window('Scenarios', parent=active_window(role='floor'), always_on_top=True)
        calls = scenarios.TKroot.method_calls
        owner_index = next(i for i, call in enumerate(calls) if call[0] == 'transient')
        top_index = next(i for i, call in enumerate(calls) if call[0] == 'wm_attributes')
        self.assertLess(owner_index, top_index)
        scenarios.TKroot.transient.assert_called_once_with(floor.TKroot)
        scenarios.TKroot.wm_attributes.assert_called_once_with('-topmost', 1)

    def test_idle_activation_is_safe_after_close(self):
        from app.ui_styles import bring_window_to_front
        window = self.window()
        root = window.TKroot
        bring_window_to_front(window)
        activate = root.after_idle.call_args.args[0]
        root.focus_force.reset_mock()
        window.TKroot = None
        activate()
        root.focus_force.assert_not_called()
