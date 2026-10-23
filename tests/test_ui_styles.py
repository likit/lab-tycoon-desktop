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
