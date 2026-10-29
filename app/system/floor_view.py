"""Experimental isometric floor editor, independent of simulation state."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import floor, hypot
from pathlib import Path
import sys
import tkinter as tk
from time import monotonic
from queue import Empty
from typing import Callable, Iterable

import FreeSimpleGUI as sg
from PIL import Image, ImageDraw, ImageFilter, ImageTk

from app.ui_styles import create_window, DANGER_BUTTON_COLOR, bring_window_to_front
from app.auth.windows import login_required
from app.project import LabTycoonProject, ProjectEquipmentInstance, ProjectFloor
from app.system.live_simulation import LiveSimulationSession, DEFAULT_SIMULATION_SPEED
from app.system.floor_status import equipment_overview, inspection_text
from app.system.floor_navigation import FloorPanController, ZoomCanvas
from app.simulation_runs import persist_generated_run
from app.system.equipment_animation import (animation_frame, FRAME_COUNT,
    equipment_frame_count, equipment_animation_frame)
from app.system.floor_playback import FloorPlayback, floor_connections, tile_path, path_position


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
    sprite_scale: float = 1.0
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
    sprite_scale=0.8,
)

CHEMISTRY_ANALYZER_METADATA = EquipmentMetadata(
    equipment_type="chemistry_analyzer",
    name="Chemistry Analyzer",
    category="Analytical",
    sprite="analyzer/chemistry_analyzer.png",
    footprint=(2, 2),
    clearance=EquipmentClearance(front=1),
    anchor="s",
    sprite_anchor_cell=(1, 1),
    sprite_subsample=5,
    workflow_capable=True,
    id_prefix="CHEM",
)

SPECIMENS_RECEPTION_METADATA = EquipmentMetadata(
    equipment_type="specimens_reception_station",
    name="Specimens Reception Station",
    category="Pre-analytical",
    sprite="reception/specimens_reception_station_empty.png",
    footprint=(1, 1),
    clearance=EquipmentClearance(front=1),
    sprite_anchor_cell=(0, 0),
    workflow_capable=True,
    id_prefix="RECP",
)


REPORTING_STATION_METADATA = EquipmentMetadata(
    equipment_type="reporting_station",
    name="Reporting Station",
    category="Post-analytical",
    sprite="reporting/reporting_station_empty.png",
    footprint=(1, 1),
    clearance=EquipmentClearance(front=1),
    sprite_anchor_cell=(0, 0),
    workflow_capable=True,
    id_prefix="REPORT",
)


EQUIPMENT_CATALOG: dict[str, EquipmentMetadata] = {
    CENTRIFUGE_METADATA.equipment_type: CENTRIFUGE_METADATA,
    REPORTING_STATION_METADATA.equipment_type: REPORTING_STATION_METADATA,
    SPECIMENS_RECEPTION_METADATA.equipment_type: SPECIMENS_RECEPTION_METADATA,
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
        self.canvas = ZoomCanvas(canvas)
        self.floor_plan = floor_plan
        self.tile_path = Path(tile_path)
        self.assets_dir = Path(assets_dir)
        self.origin_x = 0.0
        self.origin_y = 202.0
        self._tile_image = self._load_tile_image()
        self._equipment_images: dict[str, object] = {}
        self._preview_images: dict[str, object] = {}
        self._selected_images: dict[str, object] = {}
        self.playback = FloorPlayback()
        self.workflow = None
        self.show_directional_lines = True
        self._flow_icons: dict[str, object] = {}
        self._transport_images: dict[tuple, object] = {}
        self._activity_images: dict[tuple, object] = {}
        self._equipment_canvas_items: dict[str, int] = {}
        self._last_activity_frame = None
        self.live_session = None
        self.live_counts = {}
        self.live_staff = []
        self._staff_images = {}
        self.live_transfers = []
        self.selected_order_id = None
        self._last_equipment = {}

    def _load_png(self, path: Path):
        """Load an RGBA PNG without changing its size or transparency."""

        return self.canvas.master.tk.call(
            "image",
            "create",
            "photo",
            "-file",
            str(path),
        )

    def _base_equipment_sprite(self, equipment: EquipmentMetadata):
        with Image.open(self.assets_dir / equipment.sprite) as source:
            rgba = source.convert('RGBA')
        scale = equipment.sprite_subsample
        size = (round(((rgba.width + scale - 1) // scale) * equipment.sprite_scale),
                round(((rgba.height + scale - 1) // scale) * equipment.sprite_scale))
        return rgba.resize(size, Image.Resampling.LANCZOS)

    def _equipment_image(self, equipment: EquipmentMetadata):
        image = self._equipment_images.get(equipment.equipment_type)
        if image is None:
            rgba = self._base_equipment_sprite(equipment)
            image = ImageTk.PhotoImage(rgba, master=self.canvas.master)
            self._equipment_images[equipment.equipment_type] = image
        return image

    def sync_live_session(self) -> dict | None:
        if self.live_session is None:
            return None
        snapshot = self.live_session.snapshot()
        self.live_counts = snapshot['counts'] if snapshot['state'] != 'cancelled' else {}
        self.live_staff = snapshot.get('staff_assignments', []) if snapshot['state'] in ('playing', 'paused') else []
        self.playback.state = {'playing': 'playing', 'paused': 'paused'}.get(snapshot['state'], 'stopped')
        self.playback.elapsed = snapshot['time'] / self.live_session.speed
        # Drain notifications; state and transfers remain owned by the session,
        # so closing/reopening a floor view never loses simulation progress.
        while True:
            try:
                self.live_session.events.get_nowait()
            except Empty:
                break
        self.live_transfers = [transfer for transfer in snapshot['transfers']
                               if not transfer.get('delivered') or snapshot['time'] - transfer['time'] < .04]
        if snapshot['state'] not in ('playing', 'paused'):
            self.live_transfers = []
        return snapshot

    def equipment_activity(self, instance_id: str) -> bool:
        if self.live_session is None:
            return True
        clock = self.live_session.snapshot()['time']
        reception = self.live_session.equipment_types.get(instance_id) == 'specimens_reception_station'
        for node in self.workflow.graph.iter_nodes():
            if node.metadata.get('equipment_id') != instance_id:
                continue
            counts = self.live_counts.get(node.node_id, {})
            if counts.get('active', 0) > 0:
                return True
            if reception and 'last_arrived' in counts:
                if 0 <= clock - counts['last_arrived'] < 2 * self.live_session.speed:
                    return True
            if node.metadata.get('terminal_output') and counts.get('finished', 0) > 0:
                return clock - counts.get('last_finished', 0) < .03
        return False

    def _activity_image(self, equipment: EquipmentMetadata, frame: int, selected: bool):
        key = (equipment.equipment_type, frame, selected)
        if key not in self._activity_images:
            with Image.open(self.assets_dir / "animation" / f"{equipment.equipment_type}_running.png") as atlas:
                width = atlas.width // equipment_frame_count(equipment.equipment_type)
                rgba = atlas.crop((frame * width, 0, (frame + 1) * width, atlas.height)).convert("RGBA")
                if equipment.sprite_scale != 1:
                    rgba = rgba.resize((round(rgba.width * equipment.sprite_scale),
                                        round(rgba.height * equipment.sprite_scale)), Image.Resampling.LANCZOS)
            if selected:
                outline = Image.new("RGBA", rgba.size, self.EQUIPMENT_SELECTION_COLOR)
                mask = rgba.getchannel("A").point(lambda alpha: 255 if alpha > 32 else 0)
                outline.putalpha(mask.filter(ImageFilter.MaxFilter(9)))
                outline.alpha_composite(rgba)
                rgba = outline
            self._activity_images[key] = ImageTk.PhotoImage(rgba, master=self.canvas.master)
        return self._activity_images[key]

    def _instance_image(self, instance: EquipmentInstance):
        equipment = self.floor_plan.equipment_catalog[instance.equipment_type]
        selected = instance.instance_id == self.floor_plan.selected_equipment_id
        frame = equipment_animation_frame(equipment.equipment_type, self.playback.state, self.playback.elapsed)
        if equipment.equipment_type == 'specimens_reception_station' and self.live_session is not None:
            arrivals = [self.live_counts.get(node.node_id, {}).get('last_arrived')
                        for node in self.workflow.graph.iter_nodes()
                        if node.metadata.get('equipment_id') == instance.instance_id]
            arrivals = [time for time in arrivals if time is not None]
            if arrivals and frame is not None:
                elapsed = (self.live_session.snapshot()['time'] - max(arrivals)) / self.live_session.speed
                frame = min(equipment_frame_count(equipment.equipment_type) - 1, max(0, int(elapsed * 16)))
        if self.live_session is not None and self.live_session.snapshot()['state'] == 'completed':
            if any(node.metadata.get('equipment_id') == instance.instance_id and node.metadata.get('terminal_output')
                   and self.live_counts.get(node.node_id, {}).get('finished', 0) > 0
                   for node in self.workflow.graph.iter_nodes()):
                return self._activity_image(equipment, FRAME_COUNT - 1, selected)
        if frame is not None and self.equipment_activity(instance.instance_id) and (self.assets_dir / "animation" / f"{equipment.equipment_type}_running.png").exists():
            return self._activity_image(equipment, frame, selected)
        return self._selected_image(equipment) if selected else self._equipment_image(equipment)

    def render_equipment_activity(self) -> None:
        frame = (animation_frame(self.playback.state, self.playback.elapsed),
                 equipment_animation_frame('specimens_reception_station', self.playback.state, self.playback.elapsed))
        if frame == self._last_activity_frame and self.live_session is None:
            return
        self._last_activity_frame = frame
        for instance in self.floor_plan.equipment_instances:
            item = self._equipment_canvas_items.get(instance.instance_id)
            if item is not None:
                self.canvas.itemconfigure(item, image=self._instance_image(instance))

    def _flow_icon(self, kind: str):
        if kind not in self._flow_icons:
            if kind == 'data':
                # A luminous signal packet with a soft cyan halo and white core.
                image = Image.new('RGBA', (28, 28))
                glow = ImageDraw.Draw(image)
                glow.ellipse((5, 5, 23, 23), fill=(34, 211, 238, 150))
                image = image.filter(ImageFilter.GaussianBlur(3))
                core = ImageDraw.Draw(image)
                core.ellipse((10, 10, 18, 18), fill=(103, 232, 249, 235))
                core.ellipse((12, 12, 16, 16), fill=(240, 253, 255, 255))
            else:
                with Image.open(self.assets_dir / 'flow' / 'blood_specimen.png') as source:
                    image = source.convert('RGBA')
            self._flow_icons[kind] = ImageTk.PhotoImage(image, master=self.canvas.master)
        return self._flow_icons[kind]

    def _draw_data_pulse(self, path, phase: float, cord_offset: float) -> None:
        """Keep the light and its short trailing streak on the routed data cord."""
        points = []
        for distance in (.035, .02, .01, 0):
            row, col = path_position(path, max(0, phase - distance))
            x, y = grid_to_screen(row, col)
            points.extend((self.origin_x + x, self.origin_y + y + TILE_HEIGHT / 2 + cord_offset))
        self.canvas.create_line(*points, fill='#67e8f9', width=2, capstyle='round',
                                joinstyle='round', tags='workflow-flow')
        self.canvas.create_image(points[-2], points[-1], image=self._flow_icon('data'),
                                 tags='workflow-flow')

    def render_staff(self) -> None:
        self.canvas.delete('attending-staff')
        groups = {}
        for allocation in self.live_staff:
            key = (allocation['equipment_id'], allocation.get('activity', 'processing'))
            group = groups.setdefault(key, dict(allocation, staff_count=0, staff_names=[]))
            group['staff_names'].extend(allocation.get('staff_names', []))
            group['staff_count'] += allocation['staff_count']
        instances = {item.instance_id: item for item in self.floor_plan.equipment_instances}
        slots = {}
        for (equipment_id, activity), allocation in groups.items():
            instance = instances.get(equipment_id)
            if instance is None:
                continue
            equipment = self.floor_plan.equipment_catalog[instance.equipment_type]
            slot = slots.get(equipment_id, 0)
            slots[equipment_id] = slot + 1
            x, y = grid_to_screen(instance.row + equipment.footprint[0], instance.col)
            x, y = self.origin_x + x - 20 + slot * 88, self.origin_y + y + TILE_HEIGHT / 2 + 10 + slot * 8
            if equipment.equipment_type == 'reporting_station':
                # Align the typing hands with the compact standing keyboard tray.
                x += 49
                y -= 49
            elif equipment.equipment_type == 'specimens_reception_station':
                x += 14
                y -= 68
            frame = animation_frame(self.playback.state, self.playback.elapsed) or 0
            pose_activity = activity
            if equipment.equipment_type == 'specimens_reception_station' and activity == 'registration':
                # Alternate keyboard entry with the existing tube-scanning pose.
                pose_activity = 'reporting' if frame < 4 else 'registration'
            key = (allocation['gender'], pose_activity, frame)
            if key not in self._staff_images:
                with Image.open(self.assets_dir / 'staff' / f"{allocation['gender']}_{pose_activity}.png") as atlas:
                    width = atlas.width // FRAME_COUNT
                    sprite = atlas.crop((frame * width, 0, (frame + 1) * width, atlas.height))
                    self._staff_images[key] = ImageTk.PhotoImage(sprite, master=self.canvas.master)
            self.canvas.create_image(x, y, image=self._staff_images[key], anchor='s', tags='attending-staff')
            self.canvas.create_oval(x - 12, y - 196, x + 12, y - 173,
                                    fill='#1e3a8a', outline='white', tags='attending-staff')
            self.canvas.create_text(x, y - 184, text=str(allocation['staff_count']),
                                    fill='white', font=('Arial', 11, 'bold'), tags='attending-staff')
            self.canvas.create_text(x, y + 12, text=activity.title() + ('\n' + ', '.join(allocation['staff_names']) if allocation['staff_names'] else ''), fill='white',
                                    font=('Arial', 9, 'bold'), tags='attending-staff')

    def _draw_queue_badge(self, x: float, y: float, counts: dict) -> None:
        self.canvas.create_text(x, y,
            text=f"Queue {counts.get('queued', 0)} · Active {counts.get('active', 0)} · Done {counts.get('finished', 0)}",
            fill="#4ade80", font=("Arial", 10, "bold"), tags="workflow-flow")

    def _draw_transport(self, points: list[float], kind: str) -> None:
        """Draw a conveyor or data cord at 80% opacity along a workflow route."""
        padding = 16
        left, top = min(points[::2]) - padding, min(points[1::2]) - padding
        local = tuple((points[i] - left, points[i + 1] - top) for i in range(0, len(points), 2))
        key = (local, kind)
        image = self._transport_images.get(key)
        if image is None:
            overlay = Image.new("RGBA", (int(max(x for x, _ in local)) + padding + 1,
                                         int(max(y for _, y in local)) + padding + 1))
            draw = ImageDraw.Draw(overlay)
            if kind == "specimens":
                # Render in floor-plane proportions, then downsample for smooth edges.
                scale = 3
                surface = Image.new("RGBA", (overlay.width * scale, overlay.height * scale * 2))
                detail = ImageDraw.Draw(surface)
                route = [(x * scale, y * scale * 2) for x, y in local]

                def rail(offset: float, width: int, color: tuple) -> None:
                    track = [(x, y + offset * scale * 2) for x, y in route]
                    detail.line(track, fill=color, width=width * scale, joint="curve")
                    radius = width * scale / 2
                    for x, y in track:
                        detail.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

                rail(3, 17, (50, 65, 75, 204))  # Low steel chassis.
                rail(1, 17, (139, 157, 166, 204))
                rail(0, 16, (215, 225, 229, 204))
                rail(0, 11, (66, 84, 94, 204))  # Recessed rubber surface.
                # Fine belt seams, spaced continuously along the route.
                travelled = 0.0
                for (x, y), (end_x, end_y) in zip(route, route[1:]):
                    length = hypot(end_x - x, end_y - y)
                    if not length:
                        continue
                    dx, dy = (end_x - x) / length, (end_y - y) / length
                    distance = (12 * scale - travelled) % (18 * scale)
                    while distance < length:
                        cx, cy = x + dx * distance, y + dy * distance
                        detail.line([(cx - dy * 4 * scale, cy + dx * 4 * scale),
                                     (cx + dy * 4 * scale, cy - dx * 4 * scale)],
                                    fill=(104, 123, 132, 204), width=scale)
                        distance += 18 * scale
                    travelled += length
                overlay = surface.resize(overlay.size, Image.Resampling.LANCZOS)
                # Resampling can overshoot alpha around hard edges.
                overlay.putalpha(overlay.getchannel("A").point(lambda alpha: min(alpha, 204)))
            else:
                # A round insulated blue cord with a highlight and plug at each end.
                draw.line(local, fill=(23, 48, 72, 204), width=7, joint="curve")
                draw.line(local, fill=(68, 155, 210, 204), width=4, joint="curve")
                draw.line([(x, y - 1) for x, y in local], fill=(156, 215, 240, 204), width=1)
                for x, y in (local[0], local[-1]):
                    draw.rounded_rectangle((x - 5, y - 4, x + 5, y + 4), radius=2,
                                           fill=(168, 189, 199, 204), outline=(43, 67, 83, 204))
            image = ImageTk.PhotoImage(overlay, master=self.canvas.master)
            if len(self._transport_images) >= 128:
                self._transport_images.clear()
            self._transport_images[key] = image
        item = self.canvas.create_image(left, top, image=image, anchor="nw", tags="workflow-flow")
        # Infrastructure lies on the floor beneath machines, people and transfer icons.
        self.canvas.tag_lower(item)
        self.canvas.tag_raise(item, "floor-tile")

    def render_flow(self) -> None:
        """Update only the overlay during playback, leaving floor sprites cached."""
        self.canvas.delete("workflow-flow")
        if self.workflow is None:
            return
        snapshot = self.live_session.snapshot() if self.live_session else {}
        route_nodes = snapshot.get('order_routes', {}).get(self.selected_order_id, [])
        route_equipment = {self.workflow.get_node(node_id).metadata.get('equipment_id')
                           for node_id in route_nodes if self.workflow.get_node(node_id) is not None}
        instances = {item.instance_id: item for item in self.floor_plan.equipment_instances}
        for connection in floor_connections(self.workflow, set(instances)):
            path = tile_path(
                set(self.floor_plan.placement_for_instance(instances[connection.source_id]).occupied_cells()),
                set(self.floor_plan.placement_for_instance(instances[connection.target_id]).occupied_cells()),
            )
            if len(path) < 2:
                continue
            points = []
            for row, col in path:
                x, y = grid_to_screen(row, col)
                points.extend((self.origin_x + x, self.origin_y + y + TILE_HEIGHT / 2))
            kinds = [kind for kind, enabled in (("specimens", connection.output_specimens),
                                                ("data", connection.output_data)) if enabled]
            # Keep the cord beside the belt when a connection carries both outputs.
            cord_offset = 17 if len(kinds) == 2 else 0
            if self.show_directional_lines:
                for kind in kinds:
                    route = [value + (cord_offset if kind == "data" and index % 2 else 0)
                             for index, value in enumerate(points)]
                    self._draw_transport(route, kind)
            if (self.selected_order_id is not None and
                    ((connection.source_id in route_equipment and connection.target_id in route_equipment)
                     or any(str(item['specimen_id']) == self.selected_order_id
                            and item['source'] == connection.source_id and item['target'] == connection.target_id
                            for item in self.live_transfers))):
                self.canvas.create_line(*points, fill='#fbbf24', width=4, tags='workflow-flow')
            if self.playback.state == "stopped":
                continue
            if self.live_session is None:
                markers = [(kind, (self.playback.elapsed / 4 + index / 3 + lane / 6) % 1)
                           for lane, kind in enumerate(kinds) for index in range(3)]
            else:
                clock = snapshot['time']
                markers = [(kind, min(.9, (clock - transfer['time']) / .04))
                           for transfer in self.live_transfers
                           if transfer['source'] == connection.source_id and transfer['target'] == connection.target_id
                           for kind in kinds]
            for kind, phase in markers:
                if kind == 'data':
                    self._draw_data_pulse(path, phase, cord_offset)
                    continue
                row, col = path_position(path, phase)
                x, y = grid_to_screen(row, col)
                self.canvas.create_image(self.origin_x + x, self.origin_y + y + TILE_HEIGHT / 2 + (cord_offset if kind == "data" else 0),
                                         image=self._flow_icon(kind), tags="workflow-flow")
        self.render_staff()
        self.render_analyzer_capacity()
        self.render_equipment_status()

    def render_equipment_status(self) -> None:
        if self.workflow is None:
            return
        snapshot = self.live_session.snapshot() if self.live_session else {}
        types = {item.instance_id: item.equipment_type for item in self.floor_plan.equipment_instances}
        overview = equipment_overview(snapshot, self.workflow, types)
        for instance in self.floor_plan.equipment_instances:
            item = self._equipment_canvas_items.get(instance.instance_id)
            bounds = self.canvas.bbox(item) if item is not None else None
            if not bounds:
                continue
            status = overview[instance.instance_id]
            left, top, right, bottom = bounds
            x = (left + right) / 2
            y = top - (43 if instance.equipment_type == 'chemistry_analyzer' else 20)
            self.canvas.create_text(x, y, text=status['state'], fill=status['color'],
                font=('Arial', 10, 'bold'), tags='workflow-flow')
            progress = status.get('progress')
            if progress:
                operation = 'test' if progress['phase'] == 'Testing' else 'step'
                prefix = f'Next {operation}' if progress['concurrent'] > 1 else operation.title()
                label = f"{prefix}: {progress['fraction']:.0%} · {progress['remaining_seconds']:.0f}s"
                self.canvas.create_text(x, y - 32, text=label, fill='#a7f3d0',
                    font=('Arial', 9, 'bold'), tags='workflow-flow')
                self.canvas.create_rectangle(x - 42, y - 22, x + 42, y - 17,
                    fill='#172b3a', outline='#64748b', tags='workflow-flow')
                if progress['fraction'] > 0:
                    self.canvas.create_rectangle(x - 41, y - 21,
                        x - 41 + 82 * progress['fraction'], y - 18,
                        fill='#4ade80', outline='', tags='workflow-flow')
            queued = status['queued']
            if queued:
                label = self.canvas.create_text(x, bottom + 16, text=f'Waiting: {queued}',
                    fill='#fde68a', font=('Arial', 10, 'bold'), tags='workflow-flow')
                label_bounds = self.canvas.bbox(label)
                if label_bounds:
                    left, top, right, lower = label_bounds
                    backing = self.canvas.create_rectangle(left - 6, top - 3, right + 6, lower + 3,
                        fill='#172b3a', outline='#475569', tags='workflow-flow')
                    self.canvas.tag_lower(backing, label)

    def equipment_at_canvas_point(self, x, y):
        content_x, content_y = self.canvas.canvasx(x), self.canvas.canvasy(y)
        equipment = {item: key for key, item in self._equipment_canvas_items.items()}
        for item in reversed(self.canvas.find_overlapping(content_x, content_y, content_x, content_y)):
            if item in equipment:
                return equipment[item]
        cell = self.cell_at_canvas_point(x, y)
        instance = self.floor_plan.equipment_at(*cell) if cell else None
        return instance.instance_id if instance else None

    def render_analyzer_capacity(self) -> None:
        """Show occupied physical processing slots, shared across workflow nodes."""
        snapshot = self.live_session.snapshot() if self.live_session else {}
        live = snapshot.get('state') in ('playing', 'paused', 'saving')
        resources = snapshot.get('equipment_capacity', {})
        for instance in self.floor_plan.equipment_instances:
            if instance.equipment_type != 'chemistry_analyzer':
                continue
            nodes = [node for node in self.workflow.graph.iter_nodes()
                     if node.kind == 'equipment' and node.widget.enabled
                     and node.metadata.get('equipment_id') == instance.instance_id
                     and not node.metadata.get('terminal_output')]
            configured = max((max(1, int(node.widget.capacity)) for node in nodes), default=None)
            resource = resources.get(instance.instance_id, {})
            total = resource.get('total', configured) if self.live_session else configured
            used = resource.get('used', 0) if live else 0
            item = self._equipment_canvas_items.get(instance.instance_id)
            bounds = self.canvas.bbox(item) if item is not None else None
            if not bounds:
                continue
            left, top, right, _ = bounds
            x, y = (left + right) / 2, top - 18
            label = f"Capacity {used}/{total}" if total is not None else "Capacity —"
            color = '#fbbf24' if total and used >= total else '#38bdf8'
            self.canvas.create_text(x, y, text=label, fill=color,
                                    font=('Arial', 10, 'bold'), tags='workflow-flow')
            if total:
                self.canvas.create_rectangle(x - 35, y + 9, x + 35, y + 14,
                                             outline='#94a3b8', tags='workflow-flow')
                if used:
                    self.canvas.create_rectangle(x - 34, y + 10,
                                                 x - 34 + 68 * min(used / total, 1), y + 13,
                                                 fill=color, outline='', tags='workflow-flow')

    def _selected_image(self, equipment: EquipmentMetadata):
        """Cache a gold silhouette border around the equipment sprite."""
        image = self._selected_images.get(equipment.equipment_type)
        if image is None:
            rgba = self._base_equipment_sprite(equipment)
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
            rgba = self._base_equipment_sprite(equipment)
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
        content_width = (self.floor_plan.rows + self.floor_plan.columns) * TILE_WIDTH / 2 + 60
        self.origin_x = self.floor_plan.rows * TILE_WIDTH / 2 + 30 + max(0, width - content_width) / 2
        content_height = (self.floor_plan.rows + self.floor_plan.columns) * TILE_HEIGHT / 2 + self.origin_y + 72
        self.canvas.configure(scrollregion=(0, 0, max(width, content_width), content_height))
        self.render()

    def _position_view(self, world_x, world_y, pixel_x, pixel_y):
        canvas = self.canvas.widget
        left, top, right, bottom = map(float, canvas.cget('scrollregion').split())
        x = world_x * self.canvas.zoom - pixel_x
        y = world_y * self.canvas.zoom - pixel_y
        left, top = min(left, x), min(top, y)
        right = max(right, x + canvas.winfo_width())
        bottom = max(bottom, y + canvas.winfo_height())
        canvas.configure(scrollregion=(left, top, right, bottom))
        canvas.xview_moveto((x - left) / max(1, right - left))
        canvas.yview_moveto((y - top) / max(1, bottom - top))

    def set_zoom(self, value, anchor=None):
        canvas = self.canvas.widget
        px, py = anchor or (canvas.winfo_width() / 2, canvas.winfo_height() / 2)
        world_x = self.canvas.canvasx(px) - self.origin_x
        world_y = self.canvas.canvasy(py) - self.origin_y
        self.canvas.set_zoom(value)
        self.resize(canvas.winfo_width() / self.canvas.zoom)
        self._position_view(world_x + self.origin_x, world_y + self.origin_y, px, py)

    def fit_floor(self):
        canvas = self.canvas.widget
        for _ in range(3):
            bounds = self.canvas.bbox('all')
            if not bounds:
                return
            left, top, right, bottom = bounds
            value = min((canvas.winfo_width() - 40) / max(1, right - left),
                        (canvas.winfo_height() - 40) / max(1, bottom - top), self.canvas.MAX_ZOOM)
            self.set_zoom(value)
        bounds = self.canvas.bbox('all')
        self._position_view((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2,
                            canvas.winfo_width() / 2, canvas.winfo_height() / 2)

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
        x, y = self.canvas.canvasx(x), self.canvas.canvasy(y)
        row, col = screen_to_grid(x - self.origin_x, y - self.origin_y)
        return (row, col) if self.floor_plan.contains(row, col) else None

    def render(self) -> None:
        self.canvas.delete("all")
        self._equipment_canvas_items.clear()
        self.canvas.configure(background=self.BACKGROUND_COLOR)
        for row in range(self.floor_plan.rows):
            for col in range(self.floor_plan.columns):
                x, y = grid_to_screen(row, col)
                self.canvas.create_image(
                    self.origin_x + x,
                    self.origin_y + y + TILE_HEIGHT / 2,
                    image=self._tile_image,
                    tags="floor-tile",
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
            self._equipment_canvas_items[instance.instance_id] = self.canvas.create_image(
                self.origin_x + x,
                self.origin_y + y + TILE_HEIGHT / 2,
                image=self._instance_image(instance),
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

        self.render_flow()


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


def _advance_floor_frame(renderer: FloorViewRenderer, event, seconds: float) -> bool:
    """Never access Tk widgets after a window-close event."""
    if event in (sg.WIN_CLOSED, "Close"):
        renderer.playback.stop()
        return False
    if renderer.live_session is not None:
        renderer.sync_live_session()
        renderer.render_equipment_activity()
        renderer.render_flow()
        return True
    renderer.playback.advance(seconds)
    if renderer.playback.state == "playing":
        renderer.render_equipment_activity()
        renderer.render_flow()
    return True


def floor_window_geometry(screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
    """Use the available desktop area with a compact toolbar and status bar."""
    width = max(1, screen_width - 40)
    height = max(1, screen_height - 100)
    return width, height, max(100, width - 24), max(100, height - 150)


class EquipmentInspector:
    """Selectable live details; selecting an order highlights its actual route."""

    def __init__(self, canvas, renderer):
        self.canvas, self.renderer = canvas, renderer
        self.equipment_id = None
        self._text = None
        self.order_ids = []
        self.frame = tk.Frame(canvas, background='#334155', padx=8, pady=8)
        header = tk.Frame(self.frame, background='#334155')
        header.pack(fill='x')
        tk.Label(header, text='Equipment details', background='#334155', foreground='#f1f5f9',
                 font=('Arial', 12, 'bold')).pack(side='left')
        tk.Button(header, text='Close', command=self.close).pack(side='right')
        body = tk.Frame(self.frame, background='#334155')
        body.pack(fill='both', expand=True, pady=5)
        self.text = tk.Text(body, wrap='word', background='#edf3f8', foreground='#172b3a',
                            font=('Arial', 10), state='disabled', height=10)
        scroll = tk.Scrollbar(body, command=self.text.yview)
        scroll.pack(side='right', fill='y')
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(fill='both', expand=True)
        tk.Label(self.frame, text='Select an order to highlight its route', anchor='w',
                 background='#334155', foreground='#e2e8f0').pack(fill='x')
        self.orders = tk.Listbox(self.frame, height=4, exportselection=False)
        self.orders.pack(fill='x')
        self.orders.bind('<<ListboxSelect>>', self.select_order)
        for widget in (self.text, self.orders):
            widget.bind('<Delete>', lambda event: 'break')
            widget.bind('<BackSpace>', lambda event: 'break')
            widget.bind('<Escape>', lambda event: (self.close(), 'break')[1])

    def select(self, equipment_id):
        if equipment_id is None:
            self.close()
            return
        self.equipment_id = equipment_id
        self.renderer.selected_order_id = None
        self.update()
        self.reposition()
        self.renderer.render_flow()

    def reposition(self):
        if self.equipment_id is None:
            return
        width = min(345, max(1, self.canvas.winfo_width() - 24))
        height = min(430, max(1, self.canvas.winfo_height() - 24))
        self.frame.place(x=max(0, self.canvas.winfo_width() - width - 12), y=12,
                         width=width, height=height)
        self.frame.lift()

    def update(self):
        if self.equipment_id is None:
            return
        renderer = self.renderer
        snapshot = renderer.live_session.snapshot() if renderer.live_session else {}
        types = {item.instance_id: item.equipment_type for item in renderer.floor_plan.equipment_instances}
        if self.equipment_id not in types:
            self.close()
            return
        summary = equipment_overview(snapshot, renderer.workflow, types)[self.equipment_id]
        text = inspection_text(self.equipment_id, summary)
        if renderer.selected_order_id:
            text += f'\n\nHighlighted order: {renderer.selected_order_id}'
        if text != self._text:
            self.text.configure(state='normal')
            self.text.delete('1.0', 'end')
            self.text.insert('end', text)
            self.text.configure(state='disabled')
            self._text = text
        orders = list(dict.fromkeys(item['order_id'] for item in summary['jobs']))
        if orders != self.order_ids:
            self.order_ids = orders
            self.orders.delete(0, 'end')
            for order_id in orders:
                self.orders.insert('end', f'Order {order_id}')
                if order_id == renderer.selected_order_id:
                    self.orders.selection_set(len(self.orders.get(0, 'end')) - 1)

    def select_order(self, _event=None):
        selected = self.orders.curselection()
        if selected:
            self.renderer.selected_order_id = self.order_ids[selected[0]]
            self.renderer.render_flow()
            self.update()

    def close(self):
        self.equipment_id = None
        self.renderer.selected_order_id = None
        self.frame.place_forget()
        self.renderer.render_flow()


class ResultsMonitor:
    """Draggable results screen whose position is saved with the floor plan."""

    def __init__(self, canvas, floor_state, on_change):
        self.canvas, self.floor_state, self.on_change = canvas, floor_state, on_change
        self._drag_origin = None
        self.frame = tk.Frame(canvas, background='#475569', borderwidth=3, relief='raised')
        handle = tk.Label(self.frame, text='LAB MONITOR  ·  Drag to move', anchor='w',
                          background='#334155', foreground='#e2e8f0',
                          font=('Arial', 10, 'bold'), cursor='fleur', padx=6, pady=4)
        handle.pack(fill='x')
        screen = tk.Frame(self.frame, background='#edf3f8', padx=5, pady=4)
        screen.pack(fill='both', expand=True, padx=3, pady=3)
        self.text = tk.Text(screen, wrap='word', borderwidth=0, highlightthickness=0,
                            background='#edf3f8', foreground='#234256',
                            font=('Arial', 10), cursor='xterm', takefocus=True,
                            state='disabled', padx=4, pady=4)
        scrollbar = tk.Scrollbar(screen, command=self.text.yview)
        scrollbar.pack(side='right', fill='y')
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side='left', fill='both', expand=True)
        # Only the bezel moves the monitor; text remains selectable and scrollable.
        for widget in (handle, self.frame):
            widget.bind('<ButtonPress-1>', self.start_drag)
            widget.bind('<B1-Motion>', self.drag)
            widget.bind('<ButtonRelease-1>', self.end_drag)
        self.reposition()

    def reposition(self):
        width = min(320, max(1, self.canvas.winfo_width() - 24))
        height = min(174, max(1, self.canvas.winfo_height() - 24))
        x, y = self.floor_state.monitor_position or (12, 12)
        x = max(0, min(x, self.canvas.winfo_width() - width))
        y = max(0, min(y, self.canvas.winfo_height() - height))
        self.frame.place(x=x, y=y, width=width, height=height)

    def start_drag(self, event):
        self._drag_origin = (event.x_root, event.y_root, self.frame.winfo_x(), self.frame.winfo_y())
        return 'break'

    def drag(self, event):
        if self._drag_origin is not None:
            root_x, root_y, x, y = self._drag_origin
            x = max(0, min(x + event.x_root - root_x,
                           self.canvas.winfo_width() - self.frame.winfo_width()))
            y = max(0, min(y + event.y_root - root_y,
                           self.canvas.winfo_height() - self.frame.winfo_height()))
            self.frame.place_configure(x=x, y=y)
        return 'break'

    def end_drag(self, event):
        if self._drag_origin is not None:
            position = (self.frame.winfo_x(), self.frame.winfo_y())
            if position != self.floor_state.monitor_position:
                self.floor_state.monitor_position = position
                self.on_change()
            self._drag_origin = None
        return 'break'


@login_required
def create_floor_view_window(project: LabTycoonProject, *, main_workspace: bool = False,
                             on_save: Callable[[], bool] | None = None,
                             live_session=None, modal: bool = False, parent=None):
    """Open the shared floor for generated orders or a selected LIS analysis."""

    analysis_mode = bool(getattr(live_session, 'is_lis_analysis', False))
    screen_width, screen_height = sg.Window.get_screen_size()
    window_width, window_height, canvas_width, canvas_height = floor_window_geometry(screen_width, screen_height)
    if main_workspace:
        canvas_height = max(100, canvas_height - 36)
    sidebar_width = 310
    text_width = max(24, (window_width - 50) // 9)
    floor_plan = FloorPlan(floor_state=project.floor, on_change=project.mark_dirty)
    workflow_labels = {f"{item.name} [{item.workflow_id[:8]}]": item.workflow for item in project.workflows}
    settings_layout = [
        [sg.Text("Setup", font=("Arial", 16, "bold"))],
        [sg.Text("Active scenario", size=(16, 1)),
         sg.Combo([], key="-LIVE-SCENARIO-", readonly=True, enable_events=True, size=(12, 1))],
        [sg.Text("Specimens/orders", size=(16, 1)),
         sg.Input("10", key="-LIVE-COUNT-", size=(12, 1))],
        [sg.Text("Seed", size=(16, 1)),
         sg.Input("", key="-LIVE-SEED-", size=(12, 1))],
        [sg.Text("Speed (h/sec)", size=(16, 1), tooltip="Simulation hours per second"),
         sg.Input(str(DEFAULT_SIMULATION_SPEED), key="-LIVE-SPEED-", size=(12, 1), tooltip="Simulation hours per second")],
        [sg.Button("Manage Scenarios", key="-LIVE-MANAGE-SCENARIOS-")],
        [sg.Text("", key="-LIVE-SCENARIO-SUMMARY-", size=(32, 3))],
        [sg.Text("Staff for this run", size=(16, 1)),
         sg.Input("", key="-LIVE-STAFF-", size=(12, 1))],
        [sg.Text("Staff changes apply to this run only", text_color="#64748b")],
        [sg.Button("Save to scenario", key="-LIVE-SAVE-SCENARIO-")],
        [sg.Text("", key="-LIVE-STAFF-STATUS-", size=(30, 1))],
        [sg.Text("", key="-LIVE-NODES-", size=(38, 6))],
        [sg.HorizontalSeparator()],
        *(_equipment_panel_layout() if not analysis_mode else []),
    ]
    layout = [
        [sg.Column([[
            sg.Text(project.display_name, key='-LAB-PROJECT-NAME-', font=('Arial', 13, 'bold'), size=(30, 1)),
            sg.Push(),
            sg.Button('Edit Workflow', key='-LAB-WORKFLOW-'),
            sg.Button('Orders', key='-LAB-ORDERS-'),
            sg.Button('Customers', key='-LAB-CUSTOMERS-'),
            sg.Button('Results', key='-LAB-RESULTS-'),
            sg.Button('Lab Performance', key='-LAB-PERFORMANCE-'),
            sg.Button('Save Project', key='-LAB-SAVE-', visible=on_save is not None),
        ]], visible=main_workspace, expand_x=True, pad=(0, 0))],
        [sg.Button("Run details" if analysis_mode else "Setup & Equipment", key="-TOGGLE-FLOOR-PANEL-"), sg.Text("Workflow"), sg.Combo(list(workflow_labels), default_value=next(iter(workflow_labels)),
                                       key="-FLOOR-WORKFLOW-", readonly=True, enable_events=True, size=(24, 1)),
         sg.Button("Play", key="-FLOW-PLAY-", button_color=("#ffffff", "#1e3a8a"), disabled_button_color=("#374151", "#e5e7eb"), use_ttk_buttons=True), sg.Button("Pause", key="-FLOW-PAUSE-", disabled=True, button_color=("#ffffff", "#1e3a8a"), disabled_button_color=("#374151", "#e5e7eb"), use_ttk_buttons=True),
         sg.Button("Stop", key="-FLOW-STOP-", disabled=True, button_color=("#ffffff", "#1e3a8a"), disabled_button_color=("#374151", "#e5e7eb"), use_ttk_buttons=True),
        ],
        [sg.Text("Stopped", key="-FLOW-STATE-", expand_x=True),
         sg.Checkbox("Show belts & cords", default=True, key="-FLOW-SHOW-LINES-", enable_events=True)],
        [sg.Text('Animation speed'), sg.Combo(['0.5×', '1×', '5×'], default_value='1×',
            key='-FLOW-SPEED-', readonly=True, enable_events=True, size=(8, 1)),
         sg.Text('Zoom'), sg.Button('−', key='-FLOOR-ZOOM-OUT-', tooltip='Zoom out'),
         sg.Button('100%', key='-FLOOR-ZOOM-RESET-', tooltip='Reset to actual size'),
         sg.Button('+', key='-FLOOR-ZOOM-IN-', tooltip='Zoom in'),
         sg.Button('Fit Floor', key='-FLOOR-FIT-'), sg.Push(),
         sg.Text('Drag to pan · Click to inspect')],
        [sg.Canvas(key="-FLOOR-CANVAS-", size=(canvas_width, canvas_height),
                   background_color=FloorViewRenderer.BACKGROUND_COLOR, expand_x=True, expand_y=True),
         sg.pin(sg.Column(settings_layout, key="-EQUIPMENT-PANEL-", size=(sidebar_width, canvas_height),
                          scrollable=True, vertical_scroll_only=True, vertical_alignment="top",
                          expand_y=True, visible=False))],
        [sg.Text("Analyzing selected LIS orders…" if analysis_mode else "Select equipment to move, or open Setup & Equipment to add",
                 key="-FLOOR-STATUS-", size=(text_width, 1), expand_x=True)],
        [sg.Button("Move Equipment", key="-MOVE-EQUIPMENT-", visible=not analysis_mode),
         sg.Button("Remove Equipment", key="-REMOVE-EQUIPMENT-", disabled=True, visible=not analysis_mode),
         sg.Push(), sg.CloseButton("Back to Home" if main_workspace else "Close", key="Close", button_color=DANGER_BUTTON_COLOR)],
    ]
    window = create_window(
        f"Lab Tycoon — {project.name} — Floor Plan" if main_workspace else "Lab Tycoon — Floor View",
        layout,
        window_role='floor',
        independent=not modal,
        parent=parent,
        size=(window_width, window_height),
        location=(20, 30),
        margins=(4, 4),
        element_padding=(3, 2),
        resizable=True,
        modal=modal,
        finalize=True,
    )
    canvas = window["-FLOOR-CANVAS-"].TKCanvas
    # Pack fixed bottom rows before the expanding viewport. Otherwise Tk grants
    # the canvas its requested height first and clips controls on smaller windows.
    viewport_row = window['-FLOOR-CANVAS-'].ParentRowFrame
    window['Close'].ParentRowFrame.pack_configure(side='bottom', before=viewport_row)
    window['-FLOOR-STATUS-'].ParentRowFrame.pack_configure(side='bottom', before=viewport_row)
    horizontal_scroll = tk.Scrollbar(canvas.master, orient="horizontal", command=canvas.xview)
    vertical_scroll = tk.Scrollbar(canvas.master, orient="vertical", command=canvas.yview)
    horizontal_scroll.pack(side="bottom", fill="x", before=canvas)
    vertical_scroll.pack(side="right", fill="y", before=canvas)
    canvas.configure(xscrollcommand=horizontal_scroll.set, yscrollcommand=vertical_scroll.set)
    renderer = FloorViewRenderer(
        canvas,
        floor_plan,
        _asset_path("tiles", "floor_lab.png"),
        _asset_path(),
    )

    monitor = ResultsMonitor(canvas, project.floor, project.mark_dirty)
    inspector = EquipmentInspector(canvas, renderer)
    results_text = monitor.text
    feed_session = object()
    feed_sequence = 0

    def update_results_feed(session, snapshot) -> None:
        nonlocal feed_session, feed_sequence
        changed = session is not feed_session
        entries = snapshot.get('results_feed', [])
        fresh = [entry for entry in entries if changed or entry['sequence'] > feed_sequence]
        if not changed and not fresh:
            return
        results_text.configure(state='normal')
        if changed:
            results_text.delete('1.0', 'end')
            feed_session, feed_sequence = session, 0
        if fresh and feed_sequence == 0:
            results_text.delete('1.0', 'end')
        if not fresh and changed:
            results_text.insert('end', 'Waiting for analyzer data…\n')
        for entry in fresh:
            route = f"{', '.join(entry['sources'])} → {entry['target']}"
            details = (f"{', '.join(entry['tests'])} — COMPLETE" if entry['tests']
                       else 'Result data received (test details unavailable)')
            results_text.insert('end', f"{entry['time']:.3f} h · {entry['order_id']} · {route}\n"
                                f"  {details}\n")
            feed_sequence = entry['sequence']
        lines = int(results_text.index('end-1c').split('.')[0])
        if lines > 150:
            results_text.delete('1.0', f'{lines - 150}.0')
        results_text.see('end')
        results_text.configure(state='disabled')

    renderer.workflow = next(iter(workflow_labels.values()))
    if live_session is not None or project.live_simulation is not None:
        renderer.live_session = live_session or project.live_simulation
        renderer.workflow = renderer.live_session.workflow
        window['-FLOOR-WORKFLOW-'].update(value=getattr(renderer.live_session, 'workflow_key', next(iter(workflow_labels))))
        renderer.sync_live_session()

    def update_live_controls() -> None:
        session = renderer.live_session
        snapshot = session.snapshot() if session else {'state': 'stopped'}
        update_results_feed(session, snapshot)
        inspector.update()
        state = snapshot['state']
        active = state in ('playing', 'paused', 'saving')
        window["-FLOW-PLAY-"].update(disabled=state in ('playing', 'saving') or (analysis_mode and state in ('completed', 'cancelled', 'failed')), disabled_button_color=("#374151", "#e5e7eb"))
        window["-FLOW-PAUSE-"].update(disabled=state != 'playing', disabled_button_color=("#374151", "#e5e7eb"))
        window["-FLOW-STOP-"].update(disabled=not active or state == 'saving', disabled_button_color=("#374151", "#e5e7eb"))
        for key in ('-FLOOR-WORKFLOW-', '-LIVE-SCENARIO-', '-LIVE-COUNT-', '-LIVE-SEED-', '-LIVE-SPEED-', '-LIVE-STAFF-', '-LIVE-MANAGE-SCENARIOS-', '-LIVE-SAVE-SCENARIO-'):
            window[key].update(disabled=active or analysis_mode)
        detail = (f" · {snapshot.get('time', 0):.3f} h · "
                  f"{snapshot.get('orders_completed', 0)}/{snapshot.get('orders_total', 0)} orders completed · "
                  f"{snapshot.get('orders_rejected', 0)} rejected · "
                  f"{snapshot.get('tests_completed', 0)}/{snapshot.get('tests_total', 0)} tests completed") if session else ''
        if session is not None and state == 'completed' and session.result:
            run_id = getattr(session.result, 'run_id', session.result)
            detail += f" · Run {run_id[:8]} saved"
            if analysis_mode:
                summary = session.result
                window['-FLOOR-STATUS-'].update(
                    f'{summary.analyzed} tests analyzed · {summary.reported} reported · '
                    f'{summary.approved} approved · {summary.skipped} skipped · Results saved')
        preparing = analysis_mode and session is not None and not session.prepared and state not in ('failed', 'cancelled')
        window['-FLOW-STATE-'].update('Preparing selected orders…' if preparing else state.title() + detail)
        window['-LIVE-STAFF-STATUS-'].update(
            f"Free {snapshot['staff_available']} / {snapshot['staff_capacity']} staff" if session else '')
        counts = snapshot.get('counts', {}) if state in ('playing', 'paused', 'saving') else {}
        work = []
        for node in renderer.workflow.graph.iter_nodes():
            node_counts = counts.get(node.node_id, {})
            if not (node_counts.get('queued') or node_counts.get('active')):
                continue
            waiting = sum(e['node_id'] == node.node_id for e in snapshot.get('staff_waiting', []))
            equipment_wait = snapshot.get('equipment_waiting', {}).get(node.node_id, 0)
            names = [name for allocation in snapshot.get('staff_assignments', [])
                     if allocation['node_id'] == node.node_id for name in allocation.get('staff_names', [])]
            detail = f"{node.widget.name}: {equipment_wait} waiting for equipment/node capacity, {waiting} waiting for staff"
            if names:
                detail += ' · Working: ' + ', '.join(names)
            work.append(detail)
        window['-LIVE-NODES-'].update(' | '.join(work) if work else ('No pending work' if session else ''))
        if snapshot.get('error'):
            window['-FLOOR-STATUS-'].update('Simulation failed: ' + snapshot['error'])

    def selected_saved_scenario():
        # Saved settings belong to the project; a live session owns a run-local copy.
        workflow = workflow_labels[window['-FLOOR-WORKFLOW-'].get()]
        label = window['-LIVE-SCENARIO-'].get()
        return workflow, next((item for item in workflow.scenarios
            if f"{item.name} [{item.scenario_id[:8]}]" == label), workflow.active_scenario)

    def show_scenario_settings():
        from app.system.scenario_editor import scenario_settings
        workflow, scenario = selected_saved_scenario()
        settings = scenario_settings(workflow, scenario)
        window['-LIVE-STAFF-'].update(str(settings['staff_capacity']))
        window['-LIVE-SCENARIO-SUMMARY-'].update(
            f"Staff: {settings['staff_capacity']} · {settings['arrival_distribution'].title()} arrivals\n"
            f"Interval / mean: {settings['arrival_minutes']:g} min\n"
            f"Variation: equipment {settings['system_variation']:.0%}, human {settings['human_variation']:.0%}")

    def refresh_scenarios() -> None:
        workflow = workflow_labels[window['-FLOOR-WORKFLOW-'].get()]
        choices = [f"{item.name} [{item.scenario_id[:8]}]" for item in workflow.scenarios]
        active = workflow.active_scenario
        selected = f'{active.name} [{active.scenario_id[:8]}]'
        running = renderer.live_session is not None and renderer.live_session.state in ('playing', 'paused', 'saving')
        if running:
            scenario_id = renderer.live_session.scenario.scenario_id
            selected = next((label for label, item in zip(choices, workflow.scenarios)
                             if item.scenario_id == scenario_id), selected)
        window['-LIVE-SCENARIO-'].update(values=choices, value=selected)
        show_scenario_settings()
        if running:
            window['-LIVE-STAFF-'].update(str(renderer.live_session.engine.staff_capacity))

    refresh_scenarios()
    update_live_controls()

    def redraw_for_size(_event=None) -> None:
        monitor.reposition()
        inspector.reposition()
        renderer.resize(max(canvas.winfo_width(), 1) / renderer.canvas.zoom)

    def change_zoom(value=None, *, fit=False, anchor=None):
        pan.cancel()
        if fit:
            renderer.fit_floor()
        else:
            renderer.set_zoom(value, anchor=anchor)
        window['-FLOOR-ZOOM-RESET-'].update(f'{renderer.canvas.zoom * 100:.0f}%')

    def wheel_zoom(event):
        factor = 1.25 if getattr(event, 'num', None) == 4 or getattr(event, 'delta', 0) > 0 else .8
        change_zoom(renderer.canvas.zoom * factor, anchor=(event.x, event.y))
        return 'break'

    def select_tile(event) -> None:
        if analysis_mode:
            equipment_id = renderer.equipment_at_canvas_point(event.x, event.y)
            if equipment_id:
                inspector.select(equipment_id)
            return
        cell = renderer.cell_at_canvas_point(event.x, event.y)
        if floor_plan.moving_equipment_id is None and floor_plan.placement_equipment_type is None:
            equipment_id = renderer.equipment_at_canvas_point(event.x, event.y)
            if equipment_id:
                instance = next(item for item in floor_plan.equipment_instances if item.instance_id == equipment_id)
                cell = (instance.row, instance.col)
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
        inspector.select(floor_plan.selected_equipment_id)
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
        if pan.cancel():
            return 'break'
        if analysis_mode:
            window.write_event_value('Close', None)
            return
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

    removal_confirmation_open = False

    def remove_selected_equipment(_event=None) -> None:
        nonlocal removal_confirmation_open
        if analysis_mode:
            return
        if removal_confirmation_open:
            return
        instance = next((item for item in floor_plan.equipment_instances
                         if item.instance_id == floor_plan.selected_equipment_id), None)
        if instance is None:
            return
        equipment = floor_plan.equipment_catalog[instance.equipment_type]
        removal_confirmation_open = True
        try:
            answer = sg.popup_yes_no(
                f"Remove {equipment.name} ({instance.instance_id}) from the floor?\n\n"
                "Workflow nodes linked to this equipment will show Missing Equipment.",
                title="Remove Equipment",
                keep_on_top=True,
            )
        finally:
            removal_confirmation_open = False
        if answer != "Yes":
            return
        removed = floor_plan.remove_selected_equipment()
        if removed is None:
            return
        window["-REMOVE-EQUIPMENT-"].update(disabled=True)
        window["-FLOOR-STATUS-"].update(f"Removed {removed.instance_id}")
        renderer.render()

    pan = FloorPanController(canvas, select_tile,
        lambda: floor_plan.placement_equipment_type is None and floor_plan.moving_equipment_id is None)
    canvas.bind('<ButtonPress-1>', pan.press)
    canvas.bind('<B1-Motion>', pan.drag)
    canvas.bind('<ButtonRelease-1>', pan.release)
    canvas.bind('<Control-MouseWheel>', wheel_zoom)
    canvas.bind('<Control-Button-4>', wheel_zoom)
    canvas.bind('<Control-Button-5>', wheel_zoom)
    if sys.platform == 'darwin':
        canvas.bind('<Command-MouseWheel>', wheel_zoom)
    canvas.bind("<Motion>", preview_tile)
    def clear_preview(_event=None) -> None:
        if floor_plan.placement_equipment_type is not None or floor_plan.moving_equipment_id is not None:
            floor_plan.selected_cell = None
            renderer.render()
    canvas.bind("<Leave>", clear_preview)
    canvas.bind("<Configure>", redraw_for_size)
    window.TKroot.bind("<Escape>", cancel_placement, add="+")
    window.TKroot.bind("<Delete>", remove_selected_equipment, add="+")
    window.TKroot.bind("<BackSpace>", remove_selected_equipment, add="+")
    redraw_for_size()
    bring_window_to_front(window)

    panel_visible = False
    if analysis_mode:
        live_session.play()
    last_frame = monotonic()
    while True:
        event, _values = window.read(timeout=33)
        now = monotonic()
        if analysis_mode and event in (sg.WIN_CLOSED, 'Close'):
            live_session.stop()
            live_session.wait()
        if not _advance_floor_frame(renderer, event, now - last_frame):
            break
        last_frame = now
        update_live_controls()
        if event == '-LAB-PERFORMANCE-':
            from app.lab_performance import create_lab_performance_window
            create_lab_performance_window(project, live=renderer.live_session, parent=window)
            continue
        if event in ('-FLOOR-ZOOM-OUT-', '-FLOOR-ZOOM-IN-', '-FLOOR-ZOOM-RESET-', '-FLOOR-FIT-'):
            if event == '-FLOOR-FIT-':
                change_zoom(fit=True)
            else:
                value = {'-FLOOR-ZOOM-OUT-': renderer.canvas.zoom * .8,
                         '-FLOOR-ZOOM-IN-': renderer.canvas.zoom * 1.25,
                         '-FLOOR-ZOOM-RESET-': 1.0}[event]
                change_zoom(value)
            continue
        if isinstance(event, str) and event.startswith("-EQUIPMENT-") and event.endswith("-"):
            equipment_type = event.removeprefix("-EQUIPMENT-").removesuffix("-")
            floor_plan.begin_placement(equipment_type)
            window["-REMOVE-EQUIPMENT-"].update(disabled=True)
            equipment = floor_plan.equipment_catalog[equipment_type]
            window["-FLOOR-STATUS-"].update(f"Placing {equipment.name}: move over the floor and click")
            renderer.render()
        elif event == '-LAB-SAVE-':
            if on_save is not None:
                on_save()
                window['-LAB-PROJECT-NAME-'].update(project.display_name)
        elif event in ('-LAB-WORKFLOW-', '-LAB-ORDERS-', '-LAB-CUSTOMERS-', '-LAB-RESULTS-'):
            from app.project_workspace import open_lab_section
            section = {'-LAB-WORKFLOW-': 'Workflow', '-LAB-ORDERS-': 'Orders',
                       '-LAB-CUSTOMERS-': 'Customers', '-LAB-RESULTS-': 'Results'}[event]
            selected_label = _values.get('-FLOOR-WORKFLOW-', next(iter(workflow_labels)))
            workflow_id = next((item.workflow_id for item in project.workflows
                                if f"{item.name} [{item.workflow_id[:8]}]" == selected_label), 'main')
            open_lab_section(project, section, workflow_id=workflow_id)
            workflow_labels.clear()
            workflow_labels.update({f"{item.name} [{item.workflow_id[:8]}]": item.workflow
                                    for item in project.workflows})
            selected_label = selected_label if selected_label in workflow_labels else next(iter(workflow_labels))
            window['-FLOOR-WORKFLOW-'].update(values=list(workflow_labels), value=selected_label)
            if renderer.live_session is None:
                renderer.workflow = workflow_labels[selected_label]
            refresh_scenarios()
            window['-LAB-PROJECT-NAME-'].update(project.display_name)
            last_frame = monotonic()
            renderer.sync_live_session()
            renderer.render()
        elif event == '-LIVE-SCENARIO-':
            if renderer.live_session is None or renderer.live_session.state not in ('playing', 'paused', 'saving'):
                workflow, scenario = selected_saved_scenario()
                workflow.select_scenario(scenario.scenario_id)
                project.mark_dirty()
                show_scenario_settings()
        elif event in ('-LIVE-MANAGE-SCENARIOS-', '-LIVE-SAVE-SCENARIO-'):
            if renderer.live_session is not None and renderer.live_session.state in ('playing', 'paused', 'saving'):
                continue
            from app.system.scenario_editor import open_scenario_manager, configure_scenario, scenario_settings
            workflow, scenario = selected_saved_scenario()
            if event == '-LIVE-MANAGE-SCENARIOS-':
                open_scenario_manager(workflow, on_change=project.mark_dirty, parent=window)
            else:
                try:
                    settings = scenario_settings(workflow, scenario)
                    configure_scenario(workflow, name=scenario.name, description=scenario.description,
                        staff=_values['-LIVE-STAFF-'], arrival_minutes=settings['arrival_minutes'],
                        system_variation=settings['system_variation'], human_variation=settings['human_variation'],
                        source=scenario)
                    project.mark_dirty()
                    window['-FLOOR-STATUS-'].update('Staff setting saved to scenario; save the project to keep it on disk.')
                except (ValueError, TypeError) as exc:
                    sg.popup_error(str(exc), keep_on_top=True)
            refresh_scenarios()
            last_frame = monotonic()
        elif event == "-TOGGLE-FLOOR-PANEL-":
            panel_visible = not panel_visible
            canvas.configure(width=max(100, window.TKroot.winfo_width() -
                                       (sidebar_width + 45 if panel_visible else 24)))
            window["-EQUIPMENT-PANEL-"].update(visible=panel_visible)
            window.refresh()
            redraw_for_size()
        elif event == "-MOVE-EQUIPMENT-":
            if floor_plan.begin_move():
                window["-FLOOR-STATUS-"].update("Moving selected equipment: click a destination tile; Esc cancels")
                renderer.render()
            else:
                window["-FLOOR-STATUS-"].update("Select equipment on the floor first")
        elif event == "-FLOW-SHOW-LINES-":
            renderer.show_directional_lines = bool(_values[event])
            renderer.render_flow()
        elif event == '-FLOW-SPEED-':
            session = renderer.live_session
            if session is not None:
                session.set_speed(session.base_speed * {'0.5×': .5, '1×': 1, '5×': 5}[_values[event]])
        elif event in ("-FLOW-PLAY-", "-FLOW-PAUSE-", "-FLOW-STOP-", "-FLOOR-WORKFLOW-"):
            try:
                if event == '-FLOOR-WORKFLOW-':
                    renderer.live_session = None
                    project.live_simulation = None
                    renderer.workflow = workflow_labels[_values[event]]
                    renderer.playback.stop()
                    refresh_scenarios()
                elif event == '-FLOW-PLAY-':
                    session = renderer.live_session
                    if analysis_mode and session.state in ('completed', 'cancelled', 'failed'):
                        continue
                    if session is None or session.state in ('completed', 'cancelled', 'failed'):
                        count = int(_values['-LIVE-COUNT-'])
                        seed_text = str(_values['-LIVE-SEED-']).strip()
                        seed = int(seed_text) if seed_text else None
                        speed = float(_values['-LIVE-SPEED-'])
                        staff_text = str(_values['-LIVE-STAFF-']).strip()
                        staff_capacity = int(staff_text) if staff_text else None
                        selected_workflow = workflow_labels[_values['-FLOOR-WORKFLOW-']]
                        scenarios = selected_workflow.scenarios
                        labels = [f"{item.name} [{item.scenario_id[:8]}]" for item in scenarios]
                        scenario = scenarios[labels.index(_values['-LIVE-SCENARIO-'])]
                        workflow_name = next((item.name for item in project.workflows
                            if item.workflow is selected_workflow), 'Workflow')
                        def save_completed(live):
                            return persist_generated_run(project_id=project.project_id, scenario=live.scenario,
                                workflow_name=workflow_name, requested_orders=count, random_seed=seed,
                                generated_orders=live.generated_orders)
                        session = LiveSimulationSession(selected_workflow, scenario=scenario,
                            count=count, seed=seed, speed=speed, staff_capacity=staff_capacity,
                            equipment_types={item.instance_id: item.equipment_type for item in project.floor.equipment_instances},
                            on_complete=save_completed)
                        session.workflow_key = _values['-FLOOR-WORKFLOW-']
                        session.set_speed(session.base_speed * {'0.5×': .5, '1×': 1, '5×': 5}[_values.get('-FLOW-SPEED-', '1×')])
                        renderer.live_session = session
                        project.live_simulation = session
                        renderer.workflow = session.workflow
                        renderer.live_transfers.clear()
                        renderer._last_equipment.clear()
                    session.play()
                elif event == '-FLOW-PAUSE-':
                    renderer.live_session.pause()
                elif renderer.live_session is not None:
                    renderer.live_session.stop()
                renderer.sync_live_session()
                renderer.render_equipment_activity()
                renderer.render_flow()
                update_live_controls()
            except (ValueError, TypeError, IndexError) as exc:
                sg.popup_error(f'Cannot start simulation: {exc}', keep_on_top=True)
        elif event == "-REMOVE-EQUIPMENT-":
            remove_selected_equipment()

    renderer.playback.stop()
    window.close()
    if analysis_mode:
        return live_session.result
