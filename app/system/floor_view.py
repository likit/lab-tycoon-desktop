"""Experimental isometric floor editor, independent of simulation state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import floor
from pathlib import Path
import sys
from typing import Callable, Iterable

import FreeSimpleGUI as sg
from PIL import Image, ImageFilter, ImageTk

from app.ui_styles import DANGER_BUTTON_COLOR, bring_window_to_front
from app.project import LabTycoonProject, ProjectEquipmentInstance, ProjectFloor


TILE_WIDTH = 128
TILE_HEIGHT = 64
DEFAULT_FLOOR_ROWS = 10
DEFAULT_FLOOR_COLUMNS = 10


def grid_to_screen(row: int, col: int) -> tuple[float, float]:
    """Return the screen-space top point of an isometric grid cell."""

    return (
        (col - row) * (TILE_WIDTH / 2),
        (col + row) * (TILE_HEIGHT / 2),
    )


def screen_to_grid(x: float, y: float) -> tuple[int, int]:
    """Return the grid cell containing a point relative to the grid origin."""

    row = floor(y / TILE_HEIGHT - x / TILE_WIDTH)
    col = floor(y / TILE_HEIGHT + x / TILE_WIDTH)
    return row, col


@dataclass(frozen=True, slots=True)
class EquipmentClearance:
    """Directional clearance, measured in logical floor tiles."""

    left: int = 0
    right: int = 0
    rear: int = 0
    front: int = 0


@dataclass(frozen=True, slots=True)
class EquipmentMetadata:
    """Reusable logical requirements for one type of equipment."""

    equipment_type: str
    name: str
    category: str
    sprite: str
    footprint: tuple[int, int] = (1, 1)
    clearance: EquipmentClearance = EquipmentClearance()
    anchor: str = "s"
    sprite_anchor_cell: tuple[int, int] = (0, 0)
    sprite_subsample: int = 1
    workflow_capable: bool = False
    id_prefix: str = "EQ"


CENTRIFUGE_METADATA = EquipmentMetadata(
    equipment_type="centrifuge",
    name="Centrifuge",
    category="Pre-analytical",
    sprite="centrifuge/centrifuge_open.png",
    footprint=(1, 1),
    clearance=EquipmentClearance(left=1, right=1, rear=1),
    workflow_capable=True,
    id_prefix="CENT",
)

CHEMISTRY_ANALYZER_METADATA = EquipmentMetadata(
    equipment_type="chemistry_analyzer",
    name="Chemistry Analyzer",
    category="Analytical",
    sprite="analyzer/chemistry_analyzer.png",
    footprint=(2, 3),
    clearance=EquipmentClearance(front=1),
    anchor="s",
    sprite_anchor_cell=(1, 2),
    sprite_subsample=4,
    workflow_capable=True,
    id_prefix="CHEM",
)

EQUIPMENT_CATALOG: dict[str, EquipmentMetadata] = {
    CENTRIFUGE_METADATA.equipment_type: CENTRIFUGE_METADATA,
    CHEMISTRY_ANALYZER_METADATA.equipment_type: CHEMISTRY_ANALYZER_METADATA,
}


EquipmentInstance = ProjectEquipmentInstance


@dataclass(frozen=True, slots=True)
class EquipmentPlacement:
    """Candidate placement used for validation before creating an instance."""

    row: int
    col: int
    equipment: EquipmentMetadata

    def occupied_cells(self) -> Iterable[tuple[int, int]]:
        rows, columns = self.equipment.footprint
        return (
            (self.row + row_offset, self.col + column_offset)
            for row_offset in range(rows)
            for column_offset in range(columns)
        )

    def required_cells(self) -> Iterable[tuple[int, int]]:
        """Yield the complete footprint and clearance boundary envelope."""

        rows, columns = self.equipment.footprint
        clearance = self.equipment.clearance
        for required_row in range(self.row - clearance.rear, self.row + rows + clearance.front):
            for required_col in range(self.col - clearance.left, self.col + columns + clearance.right):
                yield required_row, required_col


class FloorPlan:
    """Logical selection and placement state, kept separate from rendering."""

    def __init__(
        self,
        rows: int = DEFAULT_FLOOR_ROWS,
        columns: int = DEFAULT_FLOOR_COLUMNS,
        equipment_catalog: dict[str, EquipmentMetadata] | None = None,
        floor_state: ProjectFloor | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.floor_state = floor_state or ProjectFloor(rows=rows, columns=columns)
        self.rows = self.floor_state.rows
        self.columns = self.floor_state.columns
        self.equipment_catalog = dict(equipment_catalog or EQUIPMENT_CATALOG)
        self.selected_cell: tuple[int, int] | None = None
        self.selected_equipment_id: str | None = None
        self.placement_equipment_type: str | None = None
        self.moving_equipment_id: str | None = None
        self.equipment_instances = self.floor_state.equipment_instances
        self._instance_sequences = self._derive_instance_sequences()
        self._placement_session_instance_ids: set[str] = set()
        self._on_change = on_change

    def _derive_instance_sequences(self) -> dict[str, int]:
        sequences: dict[str, int] = {}
        for instance in self.equipment_instances:
            suffix = instance.instance_id.rsplit("-", 1)[-1]
            if suffix.isdigit():
                sequences[instance.equipment_type] = max(
                    sequences.get(instance.equipment_type, 0),
                    int(suffix),
                )
        return sequences

    def _notify_change(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def contains(self, row: int, col: int) -> bool:
        return 0 <= row < self.rows and 0 <= col < self.columns

    def select(self, row: int, col: int) -> bool:
        if not self.contains(row, col):
            self.selected_cell = None
            self.selected_equipment_id = None
            return False
        self.selected_cell = (row, col)
        return True

    def equipment_at(self, row: int, col: int) -> EquipmentInstance | None:
        """Return the topmost equipment instance occupying a logical cell."""

        for instance in reversed(self.equipment_instances):
            if (row, col) in set(self.placement_for_instance(instance).occupied_cells()):
                return instance
        return None

    def select_equipment_at(self, row: int, col: int) -> EquipmentInstance | None:
        instance = self.equipment_at(row, col)
        self.selected_equipment_id = instance.instance_id if instance is not None else None
        self.selected_cell = (row, col) if self.contains(row, col) else None
        return instance

    def remove_selected_equipment(self) -> EquipmentInstance | None:
        if self.selected_equipment_id is None:
            return None
        selected_id = self.selected_equipment_id
        instance = next(
            (item for item in self.equipment_instances if item.instance_id == selected_id),
            None,
        )
        if instance is None:
            self.selected_equipment_id = None
            return None
        self.equipment_instances.remove(instance)
        self.moving_equipment_id = None
        self._placement_session_instance_ids.discard(instance.instance_id)
        self.selected_equipment_id = None
        self.selected_cell = None
        self._notify_change()
        return instance

    def begin_move(self) -> bool:
        """Start moving the selected instance without creating a new one."""
        if self.selected_equipment_id is None:
            return False
        self.finish_placement()
        self.moving_equipment_id = self.selected_equipment_id
        return True

    def move_selected_to(self, row: int, col: int) -> bool:
        instance = next((item for item in self.equipment_instances
                         if item.instance_id == self.moving_equipment_id), None)
        if instance is None:
            return False
        placement = EquipmentPlacement(row, col, self.equipment_catalog[instance.equipment_type])
        others = {cell for item in self.equipment_instances if item is not instance
                  for cell in self.placement_for_instance(item).occupied_cells()}
        if (not all(self.contains(*cell) for cell in placement.required_cells())
                or not set(placement.occupied_cells()).isdisjoint(others)):
            return False
        index = self.equipment_instances.index(instance)
        self.equipment_instances[index] = replace(instance, row=row, col=col)
        self.selected_cell = (row, col)
        self.moving_equipment_id = None
        self._notify_change()
        return True

    def occupied_cells(self) -> set[tuple[int, int]]:
        return {
            cell
            for instance in self.equipment_instances
            for cell in self.placement_for_instance(instance).occupied_cells()
        }

    def placement_for_instance(self, instance: EquipmentInstance) -> EquipmentPlacement:
        return EquipmentPlacement(
            row=instance.row,
            col=instance.col,
            equipment=self.equipment_catalog[instance.equipment_type],
        )

    def can_place(self, placement: EquipmentPlacement) -> bool:
        footprint_cells = set(placement.occupied_cells())
        required_cells = set(placement.required_cells())
        return (
            bool(footprint_cells)
            and all(self.contains(*cell) for cell in required_cells)
            and footprint_cells.isdisjoint(self.occupied_cells())
        )

    def begin_placement(self, equipment_type: str) -> None:
        if equipment_type not in self.equipment_catalog:
            raise KeyError(f"Unknown equipment type: {equipment_type}")
        self.moving_equipment_id = None
        self.placement_equipment_type = equipment_type
        self.selected_cell = None
        self.selected_equipment_id = None
        self._placement_session_instance_ids.clear()

    def cancel_placement(self) -> list[EquipmentInstance]:
        """Cancel placement mode and roll back instances placed in this session."""

        removed = [
            instance
            for instance in self.equipment_instances
            if instance.instance_id in self._placement_session_instance_ids
        ]
        self.equipment_instances[:] = [
            instance
            for instance in self.equipment_instances
            if instance.instance_id not in self._placement_session_instance_ids
        ]
        self._placement_session_instance_ids.clear()
        self.placement_equipment_type = None
        self.selected_cell = None
        self.selected_equipment_id = None
        if removed:
            self._notify_change()
        return removed

    def finish_placement(self) -> None:
        """Commit instances from the active placement session and leave the mode."""

        self._placement_session_instance_ids.clear()
        self.placement_equipment_type = None
        self.selected_cell = None

    def can_place_selected(self) -> bool:
        if self.selected_cell is None or self.placement_equipment_type is None:
            return False
        equipment = self.equipment_catalog[self.placement_equipment_type]
        return self.can_place(EquipmentPlacement(*self.selected_cell, equipment=equipment))

    def place_selected(self) -> EquipmentInstance | None:
        if not self.can_place_selected():
            return None
        assert self.selected_cell is not None
        assert self.placement_equipment_type is not None
        equipment_type = self.placement_equipment_type
        sequence = self._instance_sequences.get(equipment_type, 0) + 1
        self._instance_sequences[equipment_type] = sequence
        equipment = self.equipment_catalog[equipment_type]
        instance = EquipmentInstance(
            instance_id=f"{equipment.id_prefix}-{sequence:03d}",
            equipment_type=equipment_type,
            row=self.selected_cell[0],
            col=self.selected_cell[1],
        )
        self.equipment_instances.append(instance)
        self._placement_session_instance_ids.add(instance.instance_id)
        self._notify_change()
        return instance


class FloorViewRenderer:
    """Render a FloorPlan onto a Tkinter Canvas and translate pointer input."""

    BACKGROUND_COLOR = "#26323a"
    SELECTION_COLOR = "#33b5e5"
    INVALID_SELECTION_COLOR = "#ff5252"
    EQUIPMENT_SELECTION_COLOR = "#ffd54f"

    def __init__(
        self,
        canvas: object,
        floor_plan: FloorPlan,
        tile_path: str | Path,
        assets_dir: str | Path,
    ) -> None:
        self.canvas = canvas
        self.floor_plan = floor_plan
        self.tile_path = Path(tile_path)
        self.assets_dir = Path(assets_dir)
        self.origin_x = 0.0
        self.origin_y = 28.0
        self._tile_image = self._load_tile_image()
        self._equipment_images: dict[str, object] = {}
        self._preview_images: dict[str, object] = {}
        self._selected_images: dict[str, object] = {}

    def _load_png(self, path: Path):
        """Load an RGBA PNG without changing its size or transparency."""

        return self.canvas.master.tk.call(
            "image",
            "create",
            "photo",
            "-file",
            str(path),
        )

    def _equipment_image(self, equipment: EquipmentMetadata):
        image = self._equipment_images.get(equipment.equipment_type)
        if image is None:
            source = self._load_png(self.assets_dir / equipment.sprite)
            if equipment.sprite_subsample > 1:
                image = self.canvas.master.tk.call("image", "create", "photo")
                self.canvas.master.tk.call(
                    image,
                    "copy",
                    source,
                    "-subsample",
                    equipment.sprite_subsample,
                    equipment.sprite_subsample,
                )
                self.canvas.master.tk.call("image", "delete", source)
            else:
                image = source
            self._equipment_images[equipment.equipment_type] = image
        return image

    def _selected_image(self, equipment: EquipmentMetadata):
        """Cache a gold silhouette border around the equipment sprite."""
        image = self._selected_images.get(equipment.equipment_type)
        if image is None:
            with Image.open(self.assets_dir / equipment.sprite) as source:
                rgba = source.convert("RGBA")
            scale = equipment.sprite_subsample
            # Match Tk's integer subsampling used for the normal sprite.
            rgba = rgba.resize(((rgba.width + scale - 1) // scale,
                                (rgba.height + scale - 1) // scale), Image.Resampling.NEAREST)
            mask = rgba.getchannel("A").point(lambda alpha: 255 if alpha > 32 else 0)
            outline = Image.new("RGBA", rgba.size, self.EQUIPMENT_SELECTION_COLOR)
            outline.putalpha(mask.filter(ImageFilter.MaxFilter(9)))
            outline.alpha_composite(rgba)
            image = ImageTk.PhotoImage(outline, master=self.canvas.master)
            self._selected_images[equipment.equipment_type] = image
        return image

    def _preview_image(self, equipment: EquipmentMetadata):
        image = self._preview_images.get(equipment.equipment_type)
        if image is None:
            with Image.open(self.assets_dir / equipment.sprite) as source:
                rgba = source.convert("RGBA")
            scale = equipment.sprite_subsample
            rgba = rgba.resize(((rgba.width + scale - 1) // scale,
                                (rgba.height + scale - 1) // scale))
            rgba.putalpha(rgba.getchannel("A").point(lambda alpha: round(alpha * 0.45)))
            image = ImageTk.PhotoImage(rgba, master=self.canvas.master)
            self._preview_images[equipment.equipment_type] = image
        return image

    def _load_tile_image(self):
        image = self.canvas.master.tk.call(
            "image",
            "create",
            "photo",
            "-file",
            str(self.tile_path),
        )
        # Tk supports integer subsampling. This source scales to approximately
        # 128x64 while the logical geometry remains exactly TILE_WIDTH/HEIGHT.
        source_width = int(self.canvas.master.tk.call("image", "width", image))
        source_height = int(self.canvas.master.tk.call("image", "height", image))
        x_scale = max(1, round(source_width / TILE_WIDTH))
        y_scale = max(1, round(source_height / TILE_HEIGHT))
        scaled = self.canvas.master.tk.call(
            "image",
            "create",
            "photo",
        )
        self.canvas.master.tk.call(scaled, "copy", image, "-subsample", x_scale, y_scale)
        self.canvas.master.tk.call("image", "delete", image)
        return scaled

    def resize(self, width: int) -> None:
        self.origin_x = width / 2
        self.render()

    def grid_polygon(self, row: int, col: int) -> tuple[float, ...]:
        x, y = grid_to_screen(row, col)
        x += self.origin_x
        y += self.origin_y
        return (
            x, y,
            x + TILE_WIDTH / 2, y + TILE_HEIGHT / 2,
            x, y + TILE_HEIGHT,
            x - TILE_WIDTH / 2, y + TILE_HEIGHT / 2,
        )

    def cell_at_canvas_point(self, x: float, y: float) -> tuple[int, int] | None:
        row, col = screen_to_grid(x - self.origin_x, y - self.origin_y)
        return (row, col) if self.floor_plan.contains(row, col) else None

    def render(self) -> None:
        self.canvas.delete("all")
        self.canvas.configure(background=self.BACKGROUND_COLOR)
        for row in range(self.floor_plan.rows):
            for col in range(self.floor_plan.columns):
                x, y = grid_to_screen(row, col)
                self.canvas.create_image(
                    self.origin_x + x,
                    self.origin_y + y + TILE_HEIGHT / 2,
                    image=self._tile_image,
                )

        if self.floor_plan.selected_cell is not None:
            selection_color = (
                self.INVALID_SELECTION_COLOR
                if self.floor_plan.placement_equipment_type is not None
                and not self.floor_plan.can_place_selected()
                else self.SELECTION_COLOR
            )
            self.canvas.create_polygon(
                self.grid_polygon(*self.floor_plan.selected_cell),
                fill="",
                outline=selection_color,
                width=4,
            )

        if self.floor_plan.selected_equipment_id is not None:
            selected_instance = next(
                (
                    instance
                    for instance in self.floor_plan.equipment_instances
                    if instance.instance_id == self.floor_plan.selected_equipment_id
                ),
                None,
            )
            if selected_instance is not None:
                placement = self.floor_plan.placement_for_instance(selected_instance)
                for row, col in placement.occupied_cells():
                    self.canvas.create_polygon(
                        self.grid_polygon(row, col),
                        fill="",
                        outline=self.EQUIPMENT_SELECTION_COLOR,
                        width=5,
                    )

        for instance in self.floor_plan.equipment_instances:
            placement = self.floor_plan.placement_for_instance(instance)
            anchor_row_offset, anchor_col_offset = placement.equipment.sprite_anchor_cell
            x, y = grid_to_screen(
                instance.row + anchor_row_offset,
                instance.col + anchor_col_offset,
            )
            self.canvas.create_image(
                self.origin_x + x,
                self.origin_y + y + TILE_HEIGHT / 2,
                image=(self._selected_image(placement.equipment)
                       if instance.instance_id == self.floor_plan.selected_equipment_id
                       else self._equipment_image(placement.equipment)),
                anchor=placement.equipment.anchor,
            )

        preview_type = self.floor_plan.placement_equipment_type
        if self.floor_plan.moving_equipment_id is not None:
            moving = next((item for item in self.floor_plan.equipment_instances
                           if item.instance_id == self.floor_plan.moving_equipment_id), None)
            preview_type = moving.equipment_type if moving else None
        if preview_type is not None and self.floor_plan.selected_cell is not None:
            equipment = self.floor_plan.equipment_catalog[preview_type]
            row, col = self.floor_plan.selected_cell
            offset_row, offset_col = equipment.sprite_anchor_cell
            x, y = grid_to_screen(row + offset_row, col + offset_col)
            self.canvas.create_image(
                self.origin_x + x, self.origin_y + y + TILE_HEIGHT / 2,
                image=self._preview_image(equipment), anchor=equipment.anchor,
            )


def _asset_path(*parts: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "app" / "assets" / Path(*parts)
    return Path(__file__).resolve().parents[1] / "assets" / Path(*parts)


def _equipment_panel_layout() -> list[list[object]]:
    """Build the equipment picker entirely from the catalog registry."""

    rows: list[list[object]] = [[sg.Text("Equipment", font=("Arial", 16, "bold"))]]
    categories = sorted({equipment.category for equipment in EQUIPMENT_CATALOG.values()})
    for category in categories:
        rows.append([sg.HorizontalSeparator()])
        rows.append([sg.Text(category, font=("Arial", 12, "bold"))])
        for equipment in EQUIPMENT_CATALOG.values():
            if equipment.category != category:
                continue
            footprint_rows, footprint_columns = equipment.footprint
            rows.append(
                [
                    sg.Button(
                        equipment.name,
                        key=f"-EQUIPMENT-{equipment.equipment_type}-",
                        expand_x=True,
                    )
                ]
            )
            rows.append([sg.Text(f"{footprint_rows} x {footprint_columns}")])
    rows.extend([[sg.Push()], [sg.Text("Esc cancels placement", text_color="#666666")]])
    return rows


def create_floor_view_window(project: LabTycoonProject) -> None:
    """Open the experimental isometric floor view in its own window."""

    floor_plan = FloorPlan(floor_state=project.floor, on_change=project.mark_dirty)
    layout = [
        [
            sg.Text("Experimental Floor View", font=("Arial", 18, "bold")),
            sg.Push(),
            sg.Text("Select equipment to move, or choose equipment to add", key="-FLOOR-STATUS-"),
        ],
        [
            sg.Canvas(
                key="-FLOOR-CANVAS-",
                size=(920, 720),
                background_color=FloorViewRenderer.BACKGROUND_COLOR,
                expand_x=True,
                expand_y=True,
            ),
            sg.VSeparator(),
            sg.Column(
                _equipment_panel_layout(),
                key="-EQUIPMENT-PANEL-",
                size=(190, 720),
                vertical_alignment="top",
                expand_y=True,
            ),
        ],
        [
            sg.Button("Move Equipment", key="-MOVE-EQUIPMENT-"),
            sg.Button("Remove Equipment", key="-REMOVE-EQUIPMENT-", disabled=True),
            sg.Push(),
            sg.CloseButton("Close", button_color=DANGER_BUTTON_COLOR),
        ],
    ]
    window = sg.Window(
        "Lab Tycoon — Floor View",
        layout,
        resizable=True,
        modal=False,
        finalize=True,
    )
    canvas = window["-FLOOR-CANVAS-"].TKCanvas
    renderer = FloorViewRenderer(
        canvas,
        floor_plan,
        _asset_path("tiles", "floor_lab.png"),
        _asset_path(),
    )

    def redraw_for_size(_event=None) -> None:
        renderer.resize(max(canvas.winfo_width(), 1))

    def select_tile(event) -> None:
        cell = renderer.cell_at_canvas_point(event.x, event.y)
        if cell is None:
            floor_plan.selected_cell = None
            window["-FLOOR-STATUS-"].update("Move over a floor tile")
        elif floor_plan.moving_equipment_id is not None:
            moving_id = floor_plan.moving_equipment_id
            if floor_plan.move_selected_to(*cell):
                window["-FLOOR-STATUS-"].update(f"Moved {moving_id} to row {cell[0]}, column {cell[1]}")
            else:
                window["-FLOOR-STATUS-"].update("Cannot move here; choose another tile or press Esc")
        elif floor_plan.placement_equipment_type is not None:
            existing_instance = floor_plan.equipment_at(*cell)
            if existing_instance is not None:
                floor_plan.finish_placement()
                floor_plan.select_equipment_at(*cell)
                window["-FLOOR-STATUS-"].update(
                    f"Selected {existing_instance.instance_id} ({existing_instance.status})"
                )
                window["-REMOVE-EQUIPMENT-"].update(disabled=False)
            else:
                floor_plan.select(*cell)
                instance = floor_plan.place_selected()
                equipment = floor_plan.equipment_catalog[floor_plan.placement_equipment_type]
                if instance is None:
                    window["-FLOOR-STATUS-"].update(
                        f"Cannot place {equipment.name} at row {cell[0]}, column {cell[1]}"
                    )
                else:
                    floor_plan.finish_placement()
                    floor_plan.select_equipment_at(*cell)
                    window["-REMOVE-EQUIPMENT-"].update(disabled=False)
                    window["-FLOOR-STATUS-"].update(
                        f"Placed {instance.instance_id}; select Move Equipment to reposition it"
                    )
        else:
            instance = floor_plan.select_equipment_at(*cell)
            if instance is None:
                window["-FLOOR-STATUS-"].update(f"Selected row {cell[0]}, column {cell[1]}")
                window["-REMOVE-EQUIPMENT-"].update(disabled=True)
            else:
                window["-FLOOR-STATUS-"].update(
                    f"Selected {instance.instance_id} ({instance.status})"
                )
                window["-REMOVE-EQUIPMENT-"].update(disabled=False)
        renderer.render()

    def preview_tile(event) -> None:
        if floor_plan.placement_equipment_type is None and floor_plan.moving_equipment_id is None:
            return
        cell = renderer.cell_at_canvas_point(event.x, event.y)
        if cell == floor_plan.selected_cell:
            return
        if cell is None:
            floor_plan.selected_cell = None
            window["-FLOOR-STATUS-"].update("Move over a floor tile")
        else:
            floor_plan.select(*cell)
            if floor_plan.moving_equipment_id is not None:
                window["-FLOOR-STATUS-"].update("Click to move here; Esc cancels")
                renderer.render()
                return
            equipment = floor_plan.equipment_catalog[floor_plan.placement_equipment_type]
            validity = "Click to place" if floor_plan.can_place_selected() else "Invalid placement"
            window["-FLOOR-STATUS-"].update(
                f"{equipment.name}: {validity} at row {cell[0]}, column {cell[1]}"
            )
        renderer.render()

    def cancel_placement(_event=None) -> None:
        if floor_plan.moving_equipment_id is not None:
            floor_plan.moving_equipment_id = None
            window["-FLOOR-STATUS-"].update("Move cancelled")
            renderer.render()
            return
        if floor_plan.placement_equipment_type is None:
            return
        removed = floor_plan.cancel_placement()
        suffix = f"; removed {len(removed)} placement(s)" if removed else ""
        window["-FLOOR-STATUS-"].update(f"Placement cancelled{suffix}")
        renderer.render()

    def remove_selected_equipment(_event=None) -> None:
        removed = floor_plan.remove_selected_equipment()
        if removed is None:
            return
        window["-REMOVE-EQUIPMENT-"].update(disabled=True)
        window["-FLOOR-STATUS-"].update(f"Removed {removed.instance_id}")
        renderer.render()

    canvas.bind("<Button-1>", select_tile)
    canvas.bind("<Motion>", preview_tile)
    def clear_preview(_event=None) -> None:
        if floor_plan.placement_equipment_type is not None or floor_plan.moving_equipment_id is not None:
            floor_plan.selected_cell = None
            renderer.render()
    canvas.bind("<Leave>", clear_preview)
    canvas.bind("<Configure>", redraw_for_size)
    window.TKroot.bind_all("<Escape>", cancel_placement)
    window.TKroot.bind_all("<Delete>", remove_selected_equipment)
    window.TKroot.bind_all("<BackSpace>", remove_selected_equipment)
    redraw_for_size()
    bring_window_to_front(window)

    while True:
        event, _values = window.read()
        if event in (sg.WIN_CLOSED, "Close"):
            break
        if isinstance(event, str) and event.startswith("-EQUIPMENT-") and event.endswith("-"):
            equipment_type = event.removeprefix("-EQUIPMENT-").removesuffix("-")
            floor_plan.begin_placement(equipment_type)
            window["-REMOVE-EQUIPMENT-"].update(disabled=True)
            equipment = floor_plan.equipment_catalog[equipment_type]
            window["-FLOOR-STATUS-"].update(f"Placing {equipment.name}: move over the floor and click")
            renderer.render()
        elif event == "-MOVE-EQUIPMENT-":
            if floor_plan.begin_move():
                window["-FLOOR-STATUS-"].update("Moving selected equipment: click a destination tile; Esc cancels")
                renderer.render()
            else:
                window["-FLOOR-STATUS-"].update("Select equipment on the floor first")
        elif event == "-REMOVE-EQUIPMENT-":
            remove_selected_equipment()

    window.TKroot.unbind_all("<Escape>")
    window.TKroot.unbind_all("<Delete>")
    window.TKroot.unbind_all("<BackSpace>")
    window.close()
