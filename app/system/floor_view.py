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

from app.ui_styles import DANGER_BUTTON_COLOR, bring_window_to_front
from app.project import LabTycoonProject, ProjectEquipmentInstance, ProjectFloor
from app.system.live_simulation import LiveSimulationSession
from app.simulation_runs import persist_generated_run
from app.system.equipment_animation import animation_frame, FRAME_COUNT
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
    footprint=(2, 3),
    clearance=EquipmentClearance(front=1),
    anchor="s",
    sprite_anchor_cell=(1, 2),
    sprite_subsample=4,
    workflow_capable=True,
    id_prefix="CHEM",
)

SPECIMENS_RECEPTION_METADATA = EquipmentMetadata(
    equipment_type="specimens_reception_station",
    name="Specimens Reception Station",
    category="Pre-analytical",
    sprite="reception/specimens_reception_station_empty.png",
    footprint=(1, 2),
    clearance=EquipmentClearance(front=1),
    sprite_anchor_cell=(0, 1),
    workflow_capable=True,
    id_prefix="RECP",
)


REPORTING_STATION_METADATA = EquipmentMetadata(
    equipment_type="reporting_station",
    name="Reporting Station",
    category="Post-analytical",
    sprite="reporting/reporting_station_empty.png",
    footprint=(1, 2),
    clearance=EquipmentClearance(front=1),
    sprite_anchor_cell=(0, 1),
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
        for node in self.workflow.graph.iter_nodes():
            if node.metadata.get('equipment_id') != instance_id:
                continue
            counts = self.live_counts.get(node.node_id, {})
            if counts.get('active', 0) > 0:
                return True
            if node.metadata.get('terminal_output') and counts.get('finished', 0) > 0:
                return clock - counts.get('last_finished', 0) < .03
        return False

    def _activity_image(self, equipment: EquipmentMetadata, frame: int, selected: bool):
        key = (equipment.equipment_type, frame, selected)
        if key not in self._activity_images:
            with Image.open(self.assets_dir / "animation" / f"{equipment.equipment_type}_running.png") as atlas:
                width = atlas.width // FRAME_COUNT
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
        frame = animation_frame(self.playback.state, self.playback.elapsed)
        if self.live_session is not None and self.live_session.snapshot()['state'] == 'completed':
            if any(node.metadata.get('equipment_id') == instance.instance_id and node.metadata.get('terminal_output')
                   and self.live_counts.get(node.node_id, {}).get('finished', 0) > 0
                   for node in self.workflow.graph.iter_nodes()):
                return self._activity_image(equipment, FRAME_COUNT - 1, selected)
        if frame is not None and self.equipment_activity(instance.instance_id) and (self.assets_dir / "animation" / f"{equipment.equipment_type}_running.png").exists():
            return self._activity_image(equipment, frame, selected)
        return self._selected_image(equipment) if selected else self._equipment_image(equipment)

    def render_equipment_activity(self) -> None:
        frame = animation_frame(self.playback.state, self.playback.elapsed)
        if frame == self._last_activity_frame and self.live_session is None:
            return
        self._last_activity_frame = frame
        for instance in self.floor_plan.equipment_instances:
            item = self._equipment_canvas_items.get(instance.instance_id)
            if item is not None:
                self.canvas.itemconfigure(item, image=self._instance_image(instance))

    def _flow_icon(self, kind: str):
        if kind not in self._flow_icons:
            filename = "blood_specimen.png" if kind == "specimens" else "laboratory_report.png"
            with Image.open(self.assets_dir / "flow" / filename) as source:
                image = source.convert("RGBA")
            self._flow_icons[kind] = ImageTk.PhotoImage(image, master=self.canvas.master)
        return self._flow_icons[kind]

    def render_staff(self) -> None:
        self.canvas.delete('attending-staff')
        groups = {}
        for allocation in self.live_staff:
            key = (allocation['equipment_id'], allocation.get('activity', 'processing'))
            group = groups.setdefault(key, dict(allocation, staff_count=0))
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
                x += 42
                y -= 18
            frame = animation_frame(self.playback.state, self.playback.elapsed) or 0
            key = (allocation['gender'], activity, frame)
            if key not in self._staff_images:
                with Image.open(self.assets_dir / 'staff' / f"{allocation['gender']}_{activity}.png") as atlas:
                    width = atlas.width // FRAME_COUNT
                    sprite = atlas.crop((frame * width, 0, (frame + 1) * width, atlas.height))
                    self._staff_images[key] = ImageTk.PhotoImage(sprite, master=self.canvas.master)
            self.canvas.create_image(x, y, image=self._staff_images[key], anchor='s', tags='attending-staff')
            self.canvas.create_oval(x - 12, y - 196, x + 12, y - 173,
                                    fill='#1e3a8a', outline='white', tags='attending-staff')
            self.canvas.create_text(x, y - 184, text=str(allocation['staff_count']),
                                    fill='white', font=('Arial', 11, 'bold'), tags='attending-staff')
            self.canvas.create_text(x, y + 12, text=activity.title(), fill='white',
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
            if self.playback.state == "stopped":
                continue
            if self.live_session is None:
                markers = [(kind, (self.playback.elapsed / 4 + index / 3 + lane / 6) % 1)
                           for lane, kind in enumerate(kinds) for index in range(3)]
            else:
                clock = self.live_session.snapshot()['time']
                markers = [(kind, min(.9, (clock - transfer['time']) / .04))
                           for transfer in self.live_transfers
                           if transfer['source'] == connection.source_id and transfer['target'] == connection.target_id
                           for kind in kinds]
            for kind, phase in markers:
                row, col = path_position(path, phase)
                x, y = grid_to_screen(row, col)
                self.canvas.create_image(self.origin_x + x, self.origin_y + y + TILE_HEIGHT / 2 + (cord_offset if kind == "data" else 0),
                                         image=self._flow_icon(kind), tags="workflow-flow")
        if self.live_session is not None:
            for node in self.workflow.graph.iter_nodes():
                equipment_id = node.metadata.get('equipment_id')
                instance = instances.get(equipment_id)
                if instance is None:
                    continue
                counts = self.live_counts.get(node.node_id, {})
                x, y = grid_to_screen(instance.row, instance.col)
                self._draw_queue_badge(self.origin_x + x, self.origin_y + y + TILE_HEIGHT, counts)

        self.render_staff()

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
        self.origin_x = max(width, content_width) / 2
        content_height = (self.floor_plan.rows + self.floor_plan.columns) * TILE_HEIGHT / 2 + 100
        self.canvas.configure(scrollregion=(0, 0, max(width, content_width), content_height))
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


def create_floor_view_window(project: LabTycoonProject) -> None:
    """Open the experimental isometric floor view in its own window."""

    screen_width, screen_height = sg.Window.get_screen_size()
    window_width, window_height, canvas_width, canvas_height = floor_window_geometry(screen_width, screen_height)
    sidebar_width = 310
    text_width = max(24, (window_width - 50) // 9)
    floor_plan = FloorPlan(floor_state=project.floor, on_change=project.mark_dirty)
    workflow_labels = {f"{item.name} [{item.workflow_id[:8]}]": item.workflow for item in project.workflows}
    settings_layout = [
        [sg.Text("Setup", font=("Arial", 16, "bold"))],
        [sg.Text("Scenario", size=(16, 1)),
         sg.Combo([], key="-LIVE-SCENARIO-", readonly=True, size=(12, 1))],
        [sg.Text("Specimens/orders", size=(16, 1)),
         sg.Input("10", key="-LIVE-COUNT-", size=(12, 1))],
        [sg.Text("Seed", size=(16, 1)),
         sg.Input("", key="-LIVE-SEED-", size=(12, 1))],
        [sg.Text("Speed (h/sec)", size=(16, 1), tooltip="Simulation hours per second"),
         sg.Input("0.05", key="-LIVE-SPEED-", size=(12, 1), tooltip="Simulation hours per second")],
        [sg.Text("Available staff", size=(16, 1)),
         sg.Input("", key="-LIVE-STAFF-", size=(12, 1))],
        [sg.Text("Blank staff uses scenario settings", text_color="#64748b")],
        [sg.Text("", key="-LIVE-STAFF-STATUS-", size=(30, 1))],
        [sg.Text("", key="-LIVE-NODES-", size=(32, 2))],
        [sg.HorizontalSeparator()],
        *_equipment_panel_layout(),
    ]
    layout = [
        [sg.Button("Setup & Equipment", key="-TOGGLE-FLOOR-PANEL-"), sg.Text("Workflow"), sg.Combo(list(workflow_labels), default_value=next(iter(workflow_labels)),
                                       key="-FLOOR-WORKFLOW-", readonly=True, enable_events=True, size=(24, 1)),
         sg.Button("Play", key="-FLOW-PLAY-", button_color=("#ffffff", "#1e3a8a"), disabled_button_color=("#374151", "#e5e7eb"), use_ttk_buttons=True), sg.Button("Pause", key="-FLOW-PAUSE-", disabled=True, button_color=("#ffffff", "#1e3a8a"), disabled_button_color=("#374151", "#e5e7eb"), use_ttk_buttons=True),
         sg.Button("Stop", key="-FLOW-STOP-", disabled=True, button_color=("#ffffff", "#1e3a8a"), disabled_button_color=("#374151", "#e5e7eb"), use_ttk_buttons=True),
        ],
        [sg.Text("Stopped", key="-FLOW-STATE-", expand_x=True),
         sg.Checkbox("Show belts & cords", default=True, key="-FLOW-SHOW-LINES-", enable_events=True)],
        [sg.Canvas(key="-FLOOR-CANVAS-", size=(canvas_width, canvas_height),
                   background_color=FloorViewRenderer.BACKGROUND_COLOR, expand_x=True, expand_y=True),
         sg.pin(sg.Column(settings_layout, key="-EQUIPMENT-PANEL-", size=(sidebar_width, canvas_height),
                          scrollable=True, vertical_scroll_only=True, vertical_alignment="top",
                          expand_y=True, visible=False))],
        [sg.Text("Select equipment to move, or open Setup & Equipment to add",
                 key="-FLOOR-STATUS-", size=(text_width, 1), expand_x=True)],
        [sg.Button("Move Equipment", key="-MOVE-EQUIPMENT-"),
         sg.Button("Remove Equipment", key="-REMOVE-EQUIPMENT-", disabled=True),
         sg.Push(), sg.CloseButton("Close", button_color=DANGER_BUTTON_COLOR)],
    ]
    window = sg.Window(
        "Lab Tycoon — Floor View",
        layout,
        size=(window_width, window_height),
        location=(20, 30),
        margins=(4, 4),
        element_padding=(3, 2),
        resizable=True,
        modal=False,
        finalize=True,
    )
    canvas = window["-FLOOR-CANVAS-"].TKCanvas
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

    renderer.workflow = next(iter(workflow_labels.values()))
    if project.live_simulation is not None:
        renderer.live_session = project.live_simulation
        renderer.workflow = renderer.live_session.workflow
        window['-FLOOR-WORKFLOW-'].update(value=getattr(renderer.live_session, 'workflow_key', next(iter(workflow_labels))))
        renderer.sync_live_session()

    def update_live_controls() -> None:
        session = renderer.live_session
        snapshot = session.snapshot() if session else {'state': 'stopped'}
        state = snapshot['state']
        active = state in ('playing', 'paused', 'saving')
        window["-FLOW-PLAY-"].update(disabled=state in ('playing', 'saving'), disabled_button_color=("#374151", "#e5e7eb"))
        window["-FLOW-PAUSE-"].update(disabled=state != 'playing', disabled_button_color=("#374151", "#e5e7eb"))
        window["-FLOW-STOP-"].update(disabled=not active or state == 'saving', disabled_button_color=("#374151", "#e5e7eb"))
        for key in ('-FLOOR-WORKFLOW-', '-LIVE-SCENARIO-', '-LIVE-COUNT-', '-LIVE-SEED-', '-LIVE-SPEED-', '-LIVE-STAFF-'):
            window[key].update(disabled=active)
        detail = f" · {snapshot.get('time', 0):.3f} h · {snapshot.get('completed', 0)}/{snapshot.get('total', 0)} completed" if session else ''
        if session is not None and state == 'completed' and session.result:
            detail += f" · Run {session.result[:8]} saved"
        window['-FLOW-STATE-'].update(state.title() + detail)
        window['-LIVE-STAFF-STATUS-'].update(
            f"Free {snapshot['staff_available']} / {snapshot['staff_capacity']} staff" if session else '')
        counts = snapshot.get('counts', {}) if state in ('playing', 'paused', 'saving') else {}
        work = [f"{node.widget.name}: {counts[node.node_id]['queued']} queued, {counts[node.node_id]['active']} active"
                for node in renderer.workflow.graph.iter_nodes()
                if node.node_id in counts and (counts[node.node_id]['queued'] or counts[node.node_id]['active'])]
        window['-LIVE-NODES-'].update(' | '.join(work) if work else ('No pending work' if session else ''))
        if snapshot.get('error'):
            window['-FLOOR-STATUS-'].update('Simulation failed: ' + snapshot['error'])

    def refresh_scenarios() -> None:
        choices = [f"{item.name} [{item.scenario_id[:8]}]" for item in renderer.workflow.scenarios]
        selected = choices[0] if choices else ''
        if renderer.live_session is not None:
            scenario_id = renderer.live_session.scenario.scenario_id
            selected = next((label for label, item in zip(choices, renderer.workflow.scenarios)
                             if item.scenario_id == scenario_id), selected)
        window['-LIVE-SCENARIO-'].update(values=choices, value=selected)

    refresh_scenarios()
    update_live_controls()

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

    removal_confirmation_open = False

    def remove_selected_equipment(_event=None) -> None:
        nonlocal removal_confirmation_open
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

    canvas.bind("<Button-1>", select_tile)
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
    last_frame = monotonic()
    while True:
        event, _values = window.read(timeout=33)
        now = monotonic()
        if not _advance_floor_frame(renderer, event, now - last_frame):
            break
        last_frame = now
        update_live_controls()
        if isinstance(event, str) and event.startswith("-EQUIPMENT-") and event.endswith("-"):
            equipment_type = event.removeprefix("-EQUIPMENT-").removesuffix("-")
            floor_plan.begin_placement(equipment_type)
            window["-REMOVE-EQUIPMENT-"].update(disabled=True)
            equipment = floor_plan.equipment_catalog[equipment_type]
            window["-FLOOR-STATUS-"].update(f"Placing {equipment.name}: move over the floor and click")
            renderer.render()
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
