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
