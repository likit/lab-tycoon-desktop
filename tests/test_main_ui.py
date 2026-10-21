"""Tests for the reorganized Lab Tycoon main-window shell."""

from __future__ import annotations

import unittest

from app.main_ui import MainWindowContext, build_main_menu


class MainWindowContextTests(unittest.TestCase):
    def test_default_context_represents_demo_laboratory(self) -> None:
        context = MainWindowContext()

        self.assertEqual(context.project_name, "Demo Laboratory")
        self.assertEqual(context.laboratory_name, "Demo Laboratory")
        self.assertEqual(context.scenario_name, "Default")
        self.assertEqual(context.project_status, "Ready")


class MainMenuTests(unittest.TestCase):
    def test_menu_uses_platform_functional_areas(self) -> None:
        menu = build_main_menu()

        self.assertEqual(
            [section[0] for section in menu],
            ["File", "LIS", "Simulation", "Workflow", "Analytics", "Laboratory Design", "Tools", "User", "Help"],
        )

    def test_existing_design_and_tool_windows_have_active_entries(self) -> None:
        menu = dict(build_main_menu())

        self.assertIn("Settings...", menu["LIS"])
        self.assertIn("Floor Plan", menu["Laboratory Design"])
        self.assertNotIn("Workflow", menu["Laboratory Design"])
        self.assertIn("Add Workflow...", menu["Workflow"])
        self.assertIn("Open Workflow...", menu["Workflow"])
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
        self.assertIn("!SOPs", menu["Laboratory Design"])

    def test_user_menu_exposes_current_account_actions(self) -> None:
        menu = dict(build_main_menu())

        self.assertEqual(menu["User"], ["Profile", "Sign In", "Sign Out"])


if __name__ == "__main__":
    unittest.main()
