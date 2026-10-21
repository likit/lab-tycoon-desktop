"""Tests for the experimental isometric floor view model and transforms."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

from app.system.floor_view import FloorViewRenderer

from app.system.floor_view import (
    CHEMISTRY_ANALYZER_METADATA,
    CENTRIFUGE_METADATA,
    EQUIPMENT_CATALOG,
    EquipmentClearance,
    EquipmentInstance,
    EquipmentMetadata,
    EquipmentPlacement,
    FloorPlan,
    floor_window_geometry,
    TILE_HEIGHT,
    grid_to_screen,
    screen_to_grid,
)


class FloorWindowSizingTests(unittest.TestCase):
    def test_window_and_canvas_fit_common_laptop_displays(self):
        for screen_width, screen_height in ((1024, 768), (1280, 800), (1440, 900), (1920, 1080)):
            width, height, canvas_width, canvas_height = floor_window_geometry(screen_width, screen_height)
            self.assertLessEqual(width + 40, screen_width)
            self.assertLessEqual(height + 100, screen_height)
            self.assertLess(canvas_width, width)
            self.assertEqual(canvas_height + 150, height)
            self.assertGreater(canvas_width, width * .95)

    def test_scrolled_canvas_clicks_use_content_coordinates(self):
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.floor_plan = FloorPlan()
        renderer.origin_x, renderer.origin_y = 670, 28
        x, y = grid_to_screen(4, 5)
        content_x, content_y = x + 670, y + 28 + TILE_HEIGHT / 2
        renderer.canvas = SimpleNamespace(canvasx=lambda x: x + 200, canvasy=lambda y: y + 100)
        self.assertEqual(renderer.cell_at_canvas_point(content_x - 200, content_y - 100), (4, 5))


class TransportOverlayTests(unittest.TestCase):
    def test_belts_and_cords_are_distinct_transparent_and_cached(self):
        from unittest.mock import Mock
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.canvas = Mock(master=object())
        renderer._transport_images = {}
        captured = []
        def photo(image, **kwargs):
            captured.append(image.copy())
            return "transport"
        with patch("app.system.floor_view.ImageTk.PhotoImage", side_effect=photo):
            for kind in ("specimens", "data", "specimens", "data"):
                renderer._draw_transport([10, 10, 74, 42, 138, 10], kind)
        self.assertEqual(len(captured), 2)
        for overlay in captured:
            self.assertEqual(overlay.getchannel("A").getextrema(), (0, 204))
        self.assertNotEqual(captured[0].tobytes(), captured[1].tobytes())
        self.assertGreater(sum(bool(a) for a in captured[0].getchannel("A").tobytes()),
                           sum(bool(a) for a in captured[1].getchannel("A").tobytes()))
        self.assertEqual(renderer.canvas.tag_raise.call_count, 4)


class EquipmentPreviewTests(unittest.TestCase):
    def test_preview_preserves_transparency_and_caches_scaled_sprite(self) -> None:
        renderer = FloorViewRenderer.__new__(FloorViewRenderer)
        renderer.assets_dir = Path(__file__).resolve().parents[1] / "app" / "assets"
        renderer.canvas = SimpleNamespace(master=object())
        renderer._preview_images = {}
        captured = []
        def photo(image, **kwargs):
            captured.append(image.copy())
            return "preview"
        with patch("app.system.floor_view.ImageTk.PhotoImage", side_effect=photo):
            first = renderer._preview_image(CHEMISTRY_ANALYZER_METADATA)
            self.assertEqual(renderer._preview_image(CHEMISTRY_ANALYZER_METADATA), first)
        self.assertEqual(len(captured), 1)
        from PIL import Image
        with Image.open(renderer.assets_dir / CHEMISTRY_ANALYZER_METADATA.sprite) as source:
            self.assertEqual(captured[0].size, ((source.width + 3) // 4, (source.height + 3) // 4))
        low, high = captured[0].getchannel("A").getextrema()
        self.assertEqual(low, 0)
        self.assertEqual(high, round(255 * 0.45))


class IsometricCoordinateTests(unittest.TestCase):
    def test_grid_cell_centers_round_trip(self) -> None:
        for row in range(10):
            for col in range(10):
                x, y = grid_to_screen(row, col)
                self.assertEqual(screen_to_grid(x, y + TILE_HEIGHT / 2), (row, col))

    def test_grid_coordinates_are_independent_of_canvas_origin(self) -> None:
        self.assertEqual(grid_to_screen(0, 0), (0.0, 0.0))
        self.assertEqual(grid_to_screen(2, 5), (192.0, 224.0))


class FloorPlanTests(unittest.TestCase):
    def test_move_preserves_identity_count_and_notifies_project(self) -> None:
        changes = []
        floor = FloorPlan(on_change=lambda: changes.append(True))
        instance = EquipmentInstance("CENT-001", "centrifuge", 3, 4)
        floor.equipment_instances.append(instance)
        floor.select_equipment_at(3, 4)
        self.assertTrue(floor.begin_move())
        self.assertTrue(floor.move_selected_to(6, 6))
        self.assertEqual(len(floor.equipment_instances), 1)
        moved = floor.equipment_instances[0]
        self.assertEqual(moved.instance_id, "CENT-001")
        self.assertEqual((moved.row, moved.col), (6, 6))
        self.assertIsNone(floor.moving_equipment_id)
        self.assertFalse(floor.move_selected_to(7, 7))
        self.assertEqual(changes, [True])

    def test_invalid_move_keeps_original_and_allows_retry(self) -> None:
        floor = FloorPlan()
        instance = EquipmentInstance("CHEM-001", "chemistry_analyzer", 3, 3)
        floor.equipment_instances.extend([instance, EquipmentInstance("CENT-001", "centrifuge", 7, 7)])
        floor.select_equipment_at(4, 4)
        self.assertTrue(floor.begin_move())
        self.assertFalse(floor.move_selected_to(8, 8))
        self.assertFalse(floor.move_selected_to(6, 6))
        self.assertEqual((instance.row, instance.col), (3, 3))
        self.assertTrue(floor.move_selected_to(3, 4))
        self.assertEqual(len(floor.equipment_instances), 2)

    def test_completed_single_placement_does_not_add_on_subsequent_clicks(self) -> None:
        floor = FloorPlan()
        floor.begin_placement("centrifuge")
        floor.select(3, 4)
        instance = floor.place_selected()
        floor.finish_placement()
        floor.select(6, 6)
        self.assertIsNone(floor.place_selected())
        self.assertEqual(floor.equipment_instances, [instance])

    def test_reception_station_placement_and_workflow_metadata(self) -> None:
        equipment = EQUIPMENT_CATALOG["specimens_reception_station"]
        self.assertEqual(equipment.name, "Specimens Reception Station")
        self.assertTrue(equipment.workflow_capable)
        self.assertEqual(equipment.footprint, (1, 2))
        floor = FloorPlan()
        floor.begin_placement(equipment.equipment_type)
        floor.select(3, 4)
        station = floor.place_selected()
        self.assertEqual(station.instance_id, "RECP-001")
        self.assertEqual(floor.occupied_cells(), {(3, 4), (3, 5)})
        self.assertFalse(floor.can_place(EquipmentPlacement(9, 4, equipment)))
        self.assertFalse(floor.can_place(EquipmentPlacement(3, 9, equipment)))

    def test_catalog_defines_multi_tile_chemistry_analyzer(self) -> None:
        analyzer = EQUIPMENT_CATALOG["chemistry_analyzer"]

        self.assertIs(analyzer, CHEMISTRY_ANALYZER_METADATA)
        self.assertEqual(analyzer.name, "Chemistry Analyzer")
        self.assertEqual(analyzer.category, "Analytical")
        self.assertEqual(analyzer.sprite, "analyzer/chemistry_analyzer.png")
        self.assertEqual(analyzer.footprint, (2, 3))
        self.assertEqual(analyzer.clearance, EquipmentClearance(front=1))
        self.assertEqual(analyzer.anchor, "s")
        self.assertEqual(analyzer.sprite_anchor_cell, (1, 2))
        self.assertEqual(analyzer.sprite_subsample, 4)
        self.assertEqual(analyzer.id_prefix, "CHEM")
        self.assertTrue(analyzer.workflow_capable)

    def test_analyzer_occupied_cells_derive_from_anchor_and_footprint(self) -> None:
        placement = EquipmentPlacement(4, 5, equipment=CHEMISTRY_ANALYZER_METADATA)

        self.assertEqual(
            set(placement.occupied_cells()),
            {(4, 5), (4, 6), (4, 7), (5, 5), (5, 6), (5, 7)},
        )
        self.assertEqual(
            set(placement.required_cells()) - set(placement.occupied_cells()),
            {(6, 5), (6, 6), (6, 7)},
        )

    def test_analyzer_uses_generic_boundary_and_front_clearance_validation(self) -> None:
        floor = FloorPlan(rows=10, columns=10)

        self.assertTrue(floor.can_place(EquipmentPlacement(4, 5, CHEMISTRY_ANALYZER_METADATA)))
        self.assertFalse(floor.can_place(EquipmentPlacement(4, 8, CHEMISTRY_ANALYZER_METADATA)))
        self.assertFalse(floor.can_place(EquipmentPlacement(8, 5, CHEMISTRY_ANALYZER_METADATA)))
        self.assertTrue(floor.can_place(EquipmentPlacement(7, 7, CHEMISTRY_ANALYZER_METADATA)))

    def test_analyzer_blocks_other_equipment_across_complete_footprint(self) -> None:
        floor = FloorPlan()
        floor.begin_placement("chemistry_analyzer")
        floor.select(4, 4)
        analyzer = floor.place_selected()

        self.assertEqual(analyzer.instance_id if analyzer else None, "CHEM-001")
        self.assertFalse(floor.can_place(EquipmentPlacement(5, 6, CENTRIFUGE_METADATA)))
        self.assertFalse(floor.can_place(EquipmentPlacement(3, 3, CHEMISTRY_ANALYZER_METADATA)))

    def test_analyzer_ids_continue_from_persisted_instances(self) -> None:
        floor = FloorPlan()
        floor.equipment_instances.extend(
            [
                EquipmentInstance("CHEM-001", "chemistry_analyzer", 1, 1),
                EquipmentInstance("CHEM-002", "chemistry_analyzer", 5, 5),
            ]
        )
        floor = FloorPlan(floor_state=floor.floor_state)
        floor.begin_placement("chemistry_analyzer")
        floor.select(7, 1)

        placed = floor.place_selected()

        self.assertEqual(placed.instance_id if placed else None, "CHEM-003")

    def test_selection_rejects_cells_outside_the_floor(self) -> None:
        floor = FloorPlan(rows=10, columns=10)

        self.assertTrue(floor.select(9, 9))
        self.assertFalse(floor.select(10, 9))
        self.assertIsNone(floor.selected_cell)

    def test_places_one_by_one_equipment_on_selected_tile(self) -> None:
        floor = FloorPlan()
        floor.begin_placement("centrifuge")
        floor.select(3, 4)

        instance = floor.place_selected()

        self.assertIsNotNone(instance)
        assert instance is not None
        self.assertEqual(instance.instance_id, "CENT-001")
        self.assertEqual(instance.equipment_type, "centrifuge")
        self.assertEqual(instance.status, "idle")
        self.assertEqual(CENTRIFUGE_METADATA.footprint, (1, 1))
        self.assertEqual(
            CENTRIFUGE_METADATA.clearance,
            EquipmentClearance(left=1, right=1, rear=1),
        )
        self.assertEqual(floor.occupied_cells(), {(3, 4)})
        self.assertIsNone(floor.place_selected())

    def test_equipment_instances_receive_sequential_type_ids(self) -> None:
        floor = FloorPlan()
        floor.begin_placement("centrifuge")
        floor.select(2, 2)
        first = floor.place_selected()
        floor.select(6, 6)
        second = floor.place_selected()

        self.assertEqual(first.instance_id if first else None, "CENT-001")
        self.assertEqual(second.instance_id if second else None, "CENT-002")
        self.assertEqual(floor.placement_equipment_type, "centrifuge")

    def test_cancel_placement_clears_mode_and_candidate_cell(self) -> None:
        floor = FloorPlan()
        floor.begin_placement("centrifuge")
        floor.select(3, 4)

        floor.cancel_placement()

        self.assertIsNone(floor.placement_equipment_type)
        self.assertIsNone(floor.selected_cell)

    def test_cancel_placement_removes_instances_created_in_current_session(self) -> None:
        floor = FloorPlan()
        floor.begin_placement("centrifuge")
        floor.select(3, 4)
        placed = floor.place_selected()

        removed = floor.cancel_placement()

        self.assertEqual(removed, [placed])
        self.assertEqual(floor.equipment_instances, [])
        self.assertEqual(floor.occupied_cells(), set())

    def test_select_and_remove_equipment_by_occupied_cell(self) -> None:
        floor = FloorPlan()
        floor.begin_placement("centrifuge")
        floor.select(3, 4)
        placed = floor.place_selected()
        floor.begin_placement("centrifuge")

        selected = floor.select_equipment_at(3, 4)
        removed = floor.remove_selected_equipment()

        self.assertEqual(selected, placed)
        self.assertEqual(removed, placed)
        self.assertEqual(floor.equipment_instances, [])
        self.assertIsNone(floor.selected_equipment_id)
        self.assertEqual(floor.occupied_cells(), set())

    def test_selecting_empty_cell_clears_equipment_selection(self) -> None:
        floor = FloorPlan()
        floor.equipment_instances.append(EquipmentInstance("CENT-001", "centrifuge", 3, 4))

        self.assertIsNotNone(floor.select_equipment_at(3, 4))
        self.assertIsNone(floor.select_equipment_at(6, 6))
        self.assertIsNone(floor.selected_equipment_id)

    def test_finishing_placement_commits_session_instances(self) -> None:
        floor = FloorPlan()
        floor.begin_placement("centrifuge")
        floor.select(3, 4)
        placed = floor.place_selected()

        floor.finish_placement()
        removed = floor.cancel_placement()

        self.assertIsNotNone(placed)
        self.assertEqual(removed, [])
        self.assertEqual(floor.equipment_instances, [placed])

    def test_future_footprints_check_bounds_and_collisions(self) -> None:
        floor = FloorPlan(rows=4, columns=4)
        large_equipment = EquipmentMetadata(
            equipment_type="large",
            name="Large equipment",
            category="Testing",
            sprite="large.png",
            footprint=(2, 2),
        )
        floor.equipment_catalog["large"] = large_equipment
        floor.equipment_instances.append(EquipmentInstance("LARGE-001", "large", 1, 1))

        self.assertFalse(floor.can_place(EquipmentPlacement(2, 2, equipment=CENTRIFUGE_METADATA)))
        self.assertFalse(floor.can_place(EquipmentPlacement(3, 3, equipment=large_equipment)))
        self.assertTrue(
            floor.can_place(
                EquipmentPlacement(
                    0,
                    0,
                    equipment=EquipmentMetadata(
                        equipment_type="small",
                        name="Small equipment",
                        category="Testing",
                        sprite="small.png",
                    ),
                )
            )
        )

    def test_centrifuge_clearance_must_remain_inside_floor_boundary(self) -> None:
        floor = FloorPlan(rows=10, columns=10)

        self.assertFalse(floor.can_place(EquipmentPlacement(0, 5, equipment=CENTRIFUGE_METADATA)))
        self.assertFalse(floor.can_place(EquipmentPlacement(5, 0, equipment=CENTRIFUGE_METADATA)))
        self.assertFalse(floor.can_place(EquipmentPlacement(5, 9, equipment=CENTRIFUGE_METADATA)))
        self.assertTrue(floor.can_place(EquipmentPlacement(1, 1, equipment=CENTRIFUGE_METADATA)))
        self.assertTrue(floor.can_place(EquipmentPlacement(9, 8, equipment=CENTRIFUGE_METADATA)))

    def test_footprint_and_clearance_are_independent_metadata(self) -> None:
        equipment = EquipmentMetadata(
            equipment_type="analyzer",
            name="Analyzer",
            category="Analytical",
            sprite="analyzer.png",
            footprint=(2, 3),
            clearance=EquipmentClearance(left=1, rear=2),
        )

        self.assertEqual(equipment.footprint, (2, 3))
        self.assertEqual(equipment.clearance, EquipmentClearance(left=1, rear=2))

    def test_catalog_defines_centrifuge_without_ui_specific_defaults(self) -> None:
        centrifuge = EQUIPMENT_CATALOG["centrifuge"]

        self.assertEqual(centrifuge.name, "Centrifuge")
        self.assertEqual(centrifuge.category, "Pre-analytical")
        self.assertEqual(centrifuge.sprite, "centrifuge/centrifuge_open.png")
        self.assertEqual(centrifuge.anchor, "s")
        self.assertTrue(centrifuge.workflow_capable)


if __name__ == "__main__":
    unittest.main()
