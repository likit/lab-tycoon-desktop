"""Focused tests for project workspace actions."""

from __future__ import annotations

import unittest
from unittest.mock import patch, Mock

from app.project import LabTycoonProject
from app.project_workspace import open_project_workflow, open_project_lab, open_lab_section


class ProjectWorkspaceTests(unittest.TestCase):
    def setUp(self):
        logged_in = patch('app.auth.windows.session_manager.is_logged_in', return_value=True)
        logged_in.start()
        self.addCleanup(logged_in.stop)
        database = patch('app.auth.windows.Session')
        database.start().return_value.__enter__.return_value.scalar.return_value = Mock(active=True)
        self.addCleanup(database.stop)

    def test_go_to_lab_hides_home_and_opens_floor_as_workspace(self):
        project = LabTycoonProject.create('Demo Lab')
        home, save = Mock(), Mock()
        with patch('app.project_workspace.create_floor_view_window') as open_floor:
            open_project_lab(project, home_window=home, on_save=save)
        home.hide.assert_called_once()
        open_floor.assert_called_once_with(project, main_workspace=True, on_save=save)
        home.un_hide.assert_called_once()

    def test_home_is_restored_if_floor_fails_to_open(self):
        home = Mock()
        with patch('app.project_workspace.create_floor_view_window', side_effect=RuntimeError('Window error')):
            with self.assertRaises(RuntimeError):
                open_project_lab(LabTycoonProject.create('Demo'), home_window=home)
        home.un_hide.assert_called_once()

    def test_floor_record_buttons_open_requested_tab_without_another_floor(self):
        project = LabTycoonProject.create('Demo')
        with patch('app.project_workspace.create_project_workspace_window') as open_records:
            for section in ('Orders', 'Customers', 'Results'):
                open_lab_section(project, section)
                open_records.assert_called_with(project, initial_tab=section, show_navigation=False)

    def test_floor_workflow_button_edits_the_selected_workflow(self):
        project = LabTycoonProject.create('Demo')
        extra = project.add_workflow('Other workflow')
        with patch('app.project_workspace.open_project_workflow') as edit:
            open_lab_section(project, 'Workflow', workflow_id=extra.workflow_id)
        edit.assert_called_once_with(project, extra.workflow_id)

    def test_workflow_action_opens_the_active_project_workflow(self) -> None:
        project = LabTycoonProject.create("Demo Lab")

        with patch("app.project_workspace.create_workflow_window") as open_window:
            open_project_workflow(project)

        kwargs = open_window.call_args.kwargs
        self.assertIs(kwargs["current_document"].workflow, project.workflow)
        self.assertEqual(kwargs["current_document"].workflow_id, "main")
        self.assertIs(kwargs["equipment_instances"], project.floor.equipment_instances)
        with patch("app.project_workspace.run_project_scenario") as run_scenario:
            kwargs["run_simulation"]("scenario-id", 3, 7)
        run_scenario.assert_called_once_with(
            project, scenario_id="scenario-id", orders_number=3, random_seed=7,
        )
        project.is_dirty = False
        kwargs["on_change"]()
        self.assertTrue(project.is_dirty)

    def test_additional_workflow_opens_its_own_graph_without_simulation_action(self) -> None:
        project = LabTycoonProject.create("Demo Lab")
        extra = project.add_workflow("HIS Intake")
        with patch("app.project_workspace.create_workflow_window") as open_window:
            open_project_workflow(project, extra.workflow_id)

        kwargs = open_window.call_args.kwargs
        self.assertIs(kwargs["current_document"].workflow, extra.workflow)
        self.assertEqual(kwargs["current_document"].workflow_id, extra.workflow_id)
        self.assertIsNone(kwargs["run_simulation"])
