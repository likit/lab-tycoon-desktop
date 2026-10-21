"""Illustrative workflow playback; independent of persisted simulation orders."""
from dataclasses import dataclass

from app.system.model import Workflow


@dataclass(frozen=True)
class FloorConnection:
    source_id: str
    target_id: str
    output_data: bool
    output_specimens: bool


def floor_connections(workflow: Workflow, equipment_ids: set[str]) -> list[FloorConnection]:
    """Project graph paths onto placed equipment, traversing intermediate tasks."""
    connections = []
    for source in workflow.graph.iter_nodes():
        source_id = str(source.metadata.get("equipment_id", ""))
        if source.kind != "equipment" or source_id not in equipment_ids or source.metadata.get("terminal_output"):
            continue
        pending = [edge.target for edge in workflow.graph.outgoing_edges(source.node_id)]
        seen = {source.node_id}
        while pending:
            node_id = pending.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            node = workflow.get_node(node_id)
            if node is None:
                continue
            if node.kind == "equipment":
                target_id = str(node.metadata.get("equipment_id", ""))
                if target_id in equipment_ids and target_id != source_id:
                    connections.append(FloorConnection(
                        source_id, target_id,
                        (bool(source.metadata.get("output_data", False)) or bool(node.metadata.get("terminal_output"))) and source.widget.enabled,
                        bool(source.metadata.get("output_specimens", False)) and not node.metadata.get("terminal_output", False) and source.widget.enabled,
                    ))
                continue
            pending.extend(edge.target for edge in workflow.graph.outgoing_edges(node_id))
    return list(dict.fromkeys(connections))


def tile_path(source_cells: set[tuple[int, int]], target_cells: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Join the closest footprint tiles using adjacent row/column steps."""
    if not source_cells or not target_cells:
        return []
    start, end = min(((a, b) for a in sorted(source_cells) for b in sorted(target_cells)),
                     key=lambda pair: abs(pair[0][0] - pair[1][0]) + abs(pair[0][1] - pair[1][1]))
    row, col = start
    path = [start]
    while row != end[0]:
        row += 1 if end[0] > row else -1
        path.append((row, col))
    while col != end[1]:
        col += 1 if end[1] > col else -1
        path.append((row, col))
    return path


def path_position(path: list[tuple[int, int]], phase: float) -> tuple[float, float]:
    """Interpolate along a tile path at constant speed through each turn."""
    if not path:
        raise ValueError("A path must contain at least one tile.")
    progress = max(0, min(1, phase)) * (len(path) - 1)
    index = min(int(progress), len(path) - 1)
    if index == len(path) - 1:
        return path[index]
    fraction = progress - index
    row, col = path[index]
    next_row, next_col = path[index + 1]
    return row + (next_row - row) * fraction, col + (next_col - col) * fraction


class FloorPlayback:
    def __init__(self) -> None:
        self.state = "stopped"
        self.elapsed = 0.0

    def play(self) -> None:
        self.state = "playing"

    def pause(self) -> None:
        if self.state == "playing":
            self.state = "paused"

    def stop(self) -> None:
        self.state = "stopped"
        self.elapsed = 0.0

    def advance(self, seconds: float) -> None:
        if self.state == "playing":
            self.elapsed += max(0, seconds)
