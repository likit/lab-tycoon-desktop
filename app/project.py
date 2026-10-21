"""Domain objects and active-session state for Lab Tycoon projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.system.model import Workflow


PROJECT_FORMAT_VERSION = "1.0"
APPLICATION_VERSION = "2026.1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ProjectEquipmentInstance:
    """Serializable physical equipment placed on a project floor."""

    instance_id: str
    equipment_type: str
    row: int
    col: int
    status: str = "idle"
    orientation: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.instance_id,
            "type": self.equipment_type,
            "position": [self.row, self.col],
            "status": self.status,
            "orientation": self.orientation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ProjectEquipmentInstance":
        position = data.get("position", [0, 0])
        if not isinstance(position, list) or len(position) != 2:
            raise ValueError("Equipment position must contain row and column.")
        return cls(
            instance_id=str(data["id"]),
            equipment_type=str(data["type"]),
            row=int(position[0]),
            col=int(position[1]),
            status=str(data.get("status", "idle")),
            orientation=int(data.get("orientation", 0)),
        )


@dataclass(slots=True)
class ProjectFloor:
    """Serializable logical laboratory floor state."""

    rows: int = 10
    columns: int = 10
    equipment_instances: list[ProjectEquipmentInstance] = field(default_factory=list)

    def floor_dict(self) -> dict[str, int]:
        return {"rows": self.rows, "columns": self.columns}

    def equipment_dict(self) -> dict[str, object]:
        return {"instances": [instance.to_dict() for instance in self.equipment_instances]}

    @classmethod
    def from_dicts(
        cls,
        floor_data: dict[str, object],
        equipment_data: dict[str, object],
    ) -> "ProjectFloor":
        instances_data = equipment_data.get("instances", [])
        if not isinstance(instances_data, list):
            raise ValueError("Equipment instances must be a list.")
        if not all(isinstance(item, dict) for item in instances_data):
            raise ValueError("Every equipment instance must be a JSON object.")
        instances = [
            ProjectEquipmentInstance.from_dict(item)
            for item in instances_data
        ]
        instance_ids = [instance.instance_id for instance in instances]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("Equipment instance IDs must be unique.")
        floor = cls(
            rows=int(floor_data.get("rows", 10)),
            columns=int(floor_data.get("columns", 10)),
            equipment_instances=instances,
        )
        if floor.rows <= 0 or floor.columns <= 0:
            raise ValueError("Floor dimensions must be positive.")
        for instance in instances:
            if not (0 <= instance.row < floor.rows and 0 <= instance.col < floor.columns):
                raise ValueError(f"Equipment {instance.instance_id} is outside the floor grid.")
        return floor


@dataclass(slots=True)
class ProjectWorkflow:
    """A named workflow owned by one laboratory project."""

    workflow_id: str
    name: str
    workflow: Workflow = field(default_factory=Workflow)


@dataclass(slots=True)
class LabTycoonProject:
    """Source of truth for one configured laboratory system model."""

    project_id: str
    name: str
    description: str
    created_at: str
    modified_at: str
    format_version: str = PROJECT_FORMAT_VERSION
    floor: ProjectFloor = field(default_factory=ProjectFloor)
    workflow: Workflow = field(default_factory=Workflow)
    workflow_name: str = "Main Workflow"
    additional_workflows: list[ProjectWorkflow] = field(default_factory=list)
    is_dirty: bool = False
    file_path: Path | None = None
    live_simulation: object | None = field(default=None, repr=False, compare=False)

    @classmethod
    def create(cls, name: str, description: str = "") -> "LabTycoonProject":
        name = name.strip()
        if not name:
            raise ValueError("Project name is required.")
        timestamp = _utc_now()
        return cls(
            project_id=uuid4().hex,
            name=name,
            description=description.strip(),
            created_at=timestamp,
            modified_at=timestamp,
            is_dirty=True,
        )

    def mark_dirty(self) -> None:
        self.is_dirty = True
        self.modified_at = _utc_now()

    @property
    def workflows(self) -> list[ProjectWorkflow]:
        """Include the legacy main workflow without changing existing callers."""

        return [ProjectWorkflow("main", self.workflow_name, self.workflow), *self.additional_workflows]

    def add_workflow(self, name: str) -> ProjectWorkflow:
        name = name.strip()
        if not name:
            raise ValueError("Workflow name is required.")
        if any(item.name.casefold() == name.casefold() for item in self.workflows):
            raise ValueError("Workflow names must be unique within a project.")
        item = ProjectWorkflow(uuid4().hex, name)
        self.additional_workflows.append(item)
        self.mark_dirty()
        return item

    def get_workflow(self, workflow_id: str) -> ProjectWorkflow | None:
        return next((item for item in self.workflows if item.workflow_id == workflow_id), None)

    @property
    def display_name(self) -> str:
        return f"{self.name} *" if self.is_dirty else self.name


class ProjectSession:
    """Application-level owner of the single active project."""

    def __init__(self) -> None:
        self.active_project: LabTycoonProject | None = None

    def activate(self, project: LabTycoonProject) -> None:
        if self.active_project is not project:
            self.close()
        self.active_project = project

    def close(self) -> None:
        if self.active_project is not None and self.active_project.live_simulation is not None:
            self.active_project.live_simulation.stop()
        self.active_project = None


project_session = ProjectSession()
