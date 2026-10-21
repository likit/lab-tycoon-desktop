"""Floor playback behavior and workflow projection."""
import unittest
from unittest.mock import Mock
import FreeSimpleGUI as sg
from app.system.floor_view import _advance_floor_frame
from app.system.floor_playback import FloorPlayback, floor_connections, tile_path, path_position
from app.system.model import Workflow


class FloorPlaybackTests(unittest.TestCase):
    def test_closing_running_floor_does_not_render_destroyed_canvas(self):
        for event in (sg.WIN_CLOSED, "Close"):
            renderer = Mock()
            renderer.playback = FloorPlayback()
            renderer.playback.play()
            renderer.render_flow.side_effect = AssertionError("Canvas already destroyed")
            renderer.render_equipment_activity.side_effect = AssertionError("Canvas already destroyed")
            self.assertFalse(_advance_floor_frame(renderer, event, .033))
            self.assertEqual(renderer.playback.state, "stopped")
            renderer.render_flow.assert_not_called()
            renderer.render_equipment_activity.assert_not_called()

    def test_tile_route_snaps_to_adjacent_cells_and_footprints(self):
        path = tile_path({(2, 2), (2, 3)}, {(5, 6), (5, 7)})
        self.assertEqual(path[0], (2, 3))
        self.assertEqual(path[-1], (5, 6))
        for a, b in zip(path, path[1:]):
            self.assertEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1)
        self.assertEqual(path_position(path, 0), path[0])
        self.assertEqual(path_position(path, 1), path[-1])
        self.assertEqual(path_position([(2, 3), (3, 3), (3, 4)], 0.75), (3, 3.5))
        self.assertEqual(tile_path(set(), {(1, 1)}), [])

    def test_reporting_endpoint_projects_intermediate_processing_as_paper_flow(self):
        workflow = Workflow()
        analyzer = workflow.create_node("Analyzer", kind="equipment",
            metadata={"equipment_id": "CHEM-001", "output_specimens": True})
        reporting = workflow.create_connected_node(analyzer.node_id, "Reporting")
        validating = workflow.create_connected_node(reporting.node_id, "Validating")
        workflow.create_connected_node(validating.node_id, "Reporting Station", kind="equipment",
            metadata={"equipment_id": "REPORT-001", "terminal_output": True})
        links = floor_connections(workflow, {"CHEM-001", "REPORT-001"})
        self.assertEqual(len(links), 1)
        self.assertEqual((links[0].source_id, links[0].target_id), ("CHEM-001", "REPORT-001"))
        self.assertTrue(links[0].output_data)
        self.assertFalse(links[0].output_specimens)

    def test_pause_resume_stop(self):
        playback = FloorPlayback()
        playback.advance(2)
        self.assertEqual(playback.elapsed, 0)
        playback.play()
        playback.advance(2)
        playback.pause()
        playback.advance(10)
        self.assertEqual(playback.elapsed, 2)
        playback.play()
        playback.advance(1)
        self.assertEqual(playback.elapsed, 3)
        playback.stop()
        self.assertEqual((playback.state, playback.elapsed), ("stopped", 0))

    def test_projects_paths_and_independent_outputs_and_skips_missing_equipment(self):
        workflow = Workflow()
        source = workflow.create_node("Reception", kind="equipment", metadata={
            "equipment_id": "RECP-001", "output_specimens": True, "output_data": False})
        middle = workflow.create_connected_node(source.node_id, "Task")
        target = workflow.create_connected_node(middle.node_id, "Analyzer", kind="equipment",
                                                metadata={"equipment_id": "CHEM-001"})
        workflow.graph.connect_nodes(middle.node_id, source.node_id)
        links = floor_connections(workflow, {"RECP-001", "CHEM-001"})
        self.assertEqual(len(links), 1)
        self.assertEqual((links[0].source_id, links[0].target_id), ("RECP-001", "CHEM-001"))
        self.assertTrue(links[0].output_specimens)
        self.assertFalse(links[0].output_data)
        source.metadata["output_data"] = True
        self.assertTrue(floor_connections(workflow, {"RECP-001", "CHEM-001"})[0].output_data)
        self.assertEqual(floor_connections(workflow, {"RECP-001"}), [])
        source.widget.enabled = False
        self.assertFalse(floor_connections(workflow, {"RECP-001", "CHEM-001"})[0].output_specimens)
