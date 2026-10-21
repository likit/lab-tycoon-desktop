"""Focused tests for project workspace actions."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.project import LabTycoonProject
from app.project_workspace import open_project_workflow


class ProjectWorkspaceTests(unittest.TestCase):
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
