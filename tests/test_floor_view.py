"""Tests for the experimental isometric floor view model and transforms."""

from __future__ import annotations

import unittest

from app.system.floor_view import (
    CENTRIFUGE_METADATA,
    EQUIPMENT_CATALOG,
    EquipmentClearance,
    EquipmentInstance,
    EquipmentMetadata,
    EquipmentPlacement,
    FloorPlan,
    TILE_HEIGHT,
    grid_to_screen,
    screen_to_grid,
)


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
