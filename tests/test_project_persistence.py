"""Project-domain and .ltp archive persistence tests."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from app.project import LabTycoonProject, ProjectEquipmentInstance, project_session
from app.project_serializer import ProjectFormatError, ProjectSerializer, REQUIRED_MEMBERS
from app.system.floor_view import FloorPlan
from app.system.model import Scenario


class ProjectPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.serializer = ProjectSerializer()
        self.project = LabTycoonProject.create("Clinical Chemistry", "Teaching laboratory")
        self.project.floor.equipment_instances.extend(
            [
                ProjectEquipmentInstance("CENT-001", "centrifuge", 2, 2),
                ProjectEquipmentInstance("CENT-003", "centrifuge", 5, 5, status="maintenance"),
            ]
        )
        first = self.project.workflow.create_node("Receive", metadata={"x": 120, "y": 80})
        second = self.project.workflow.create_connected_node(
            first.node_id,
            "Centrifuge",
            service_time=4.5,
            metadata={"equipment_instance_id": "CENT-003", "x": 360, "y": 80},
        )
        self.project.workflow.graph.edges[0].label = "serum"
        self.project.workflow.scenarios.append(
            Scenario(name="Night Shift", parameters={"staff": 2})
        )

    def tearDown(self) -> None:
        project_session.close()

    def test_new_project_has_identity_metadata_default_models_and_dirty_state(self) -> None:
        project = LabTycoonProject.create("Test Chemistry Laboratory", "Acceptance project")

        self.assertTrue(project.project_id)
        self.assertEqual(project.name, "Test Chemistry Laboratory")
        self.assertEqual(project.description, "Acceptance project")
        self.assertTrue(project.created_at)
        self.assertEqual(project.created_at, project.modified_at)
        self.assertEqual((project.floor.rows, project.floor.columns), (10, 10))
        self.assertEqual(project.workflow.get_node_ids(), [])
        self.assertTrue(project.is_dirty)
        self.assertIsNone(project.file_path)

    def test_complete_create_edit_save_close_open_continue_cycle(self) -> None:
        project = LabTycoonProject.create("Test Chemistry Laboratory")
        project_session.activate(project)
        floor = FloorPlan(floor_state=project.floor, on_change=project.mark_dirty)
        floor.begin_placement("centrifuge")
        for row, col in ((2, 2), (5, 5)):
            floor.select(row, col)
            floor.place_selected()
        first = project.workflow.create_node("Receive")
        project.workflow.create_connected_node(first.node_id, "Process")
        project.mark_dirty()

        with tempfile.TemporaryDirectory() as directory:
            path = self.serializer.save(project, Path(directory) / "test_chemistry.ltp")
            project_session.close()
            reopened = self.serializer.load(path)
            project_session.activate(reopened)

            self.assertEqual(len(reopened.floor.equipment_instances), 2)
            self.assertEqual(len(reopened.workflow.graph.nodes), 2)
            self.assertEqual(len(reopened.workflow.graph.edges), 1)
            continued_floor = FloorPlan(
                floor_state=reopened.floor,
                on_change=reopened.mark_dirty,
            )
            continued_floor.begin_placement("centrifuge")
            continued_floor.select(8, 7)
            self.assertEqual(continued_floor.place_selected().instance_id, "CENT-003")
            self.serializer.save(reopened, path)
            final = self.serializer.load(path)

        self.assertEqual(len(final.floor.equipment_instances), 3)
        self.assertFalse(final.is_dirty)

    def test_save_creates_complete_zip_archive_and_clears_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            saved_path = self.serializer.save(self.project, Path(directory) / "chemistry")

            self.assertEqual(saved_path.suffix, ".ltp")
            self.assertFalse(self.project.is_dirty)
            self.assertEqual(self.project.file_path, saved_path)
            with ZipFile(saved_path) as archive:
                self.assertTrue(REQUIRED_MEMBERS.issubset(archive.namelist()))
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["format"], "labtycoon-project")
                self.assertEqual(manifest["format_version"], "1.0")

    def test_round_trip_preserves_metadata_floor_equipment_and_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            saved_path = self.serializer.save(self.project, Path(directory) / "chemistry.ltp")
            loaded = self.serializer.load(saved_path)

        self.assertEqual(loaded.project_id, self.project.project_id)
        self.assertEqual(loaded.name, "Clinical Chemistry")
        self.assertEqual(loaded.description, "Teaching laboratory")
        self.assertEqual(loaded.floor.floor_dict(), {"rows": 10, "columns": 10})
        self.assertEqual(loaded.floor.equipment_instances, self.project.floor.equipment_instances)
        self.assertEqual(loaded.workflow.get_node_ids(), self.project.workflow.get_node_ids())
        self.assertEqual(
            [(edge.source, edge.target, edge.label) for edge in loaded.workflow.graph.edges],
            [(edge.source, edge.target, edge.label) for edge in self.project.workflow.graph.edges],
        )
        loaded_node = loaded.workflow.get_node(loaded.workflow.get_node_ids()[1])
        self.assertEqual(loaded_node.metadata["equipment_instance_id"], "CENT-003")
        self.assertEqual(loaded_node.metadata["x"], 360)
        self.assertEqual(loaded_node.metadata["y"], 80)
        self.assertEqual(loaded.workflow.scenarios[-1].parameters, {"staff": 2})
        self.assertFalse(loaded.is_dirty)

    def test_loaded_equipment_ids_continue_without_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            loaded = self.serializer.load(
                self.serializer.save(self.project, Path(directory) / "chemistry.ltp")
            )

        floor = FloorPlan(floor_state=loaded.floor)
        floor.begin_placement("centrifuge")
        floor.select(8, 7)

        self.assertEqual(floor.place_selected().instance_id, "CENT-004")

    def test_floor_mutation_marks_project_dirty(self) -> None:
        self.project.is_dirty = False
        floor = FloorPlan(floor_state=self.project.floor, on_change=self.project.mark_dirty)
        floor.begin_placement("centrifuge")
        floor.select(8, 7)

        floor.place_selected()

        self.assertTrue(self.project.is_dirty)

    def test_malformed_archive_is_rejected_without_replacing_existing_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.ltp"
            path.write_text("not a zip", encoding="utf-8")

            with self.assertRaises(ProjectFormatError):
                self.serializer.load(path)

        self.assertEqual(self.project.name, "Clinical Chemistry")

    def test_archive_with_invalid_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.ltp"
            with ZipFile(path, "w") as archive:
                for member in REQUIRED_MEMBERS:
                    payload = {"format": "some-other-format"} if member == "manifest.json" else {}
                    archive.writestr(member, json.dumps(payload))

            with self.assertRaisesRegex(ProjectFormatError, "manifest format"):
                self.serializer.load(path)


if __name__ == "__main__":
    unittest.main()
