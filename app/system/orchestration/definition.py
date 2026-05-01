"""Minimal external workflow definition loading and representation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """A small BPMN-like workflow step used by the stub runtime."""

    step_id: str
    step_type: str
    action: str | None = None
    condition: dict[str, Any] | None = None
    next_step: str | None = None
    true_next: str | None = None
    false_next: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    """An externalized executable workflow definition."""

    process_name: str
    version: str
    start_step: str
    steps: dict[str, WorkflowStep]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowDefinition":
        """Build a workflow definition from decoded JSON data."""

        steps = {}
        for step_data in data.get("steps", []):
            step = WorkflowStep(
                step_id=str(step_data["id"]),
                step_type=str(step_data["type"]),
                action=step_data.get("action"),
                condition=step_data.get("condition"),
                next_step=step_data.get("next"),
                true_next=step_data.get("true_next"),
                false_next=step_data.get("false_next"),
            )
            steps[step.step_id] = step

        return cls(
            process_name=str(data["process_name"]),
            version=str(data.get("version", "0.1")),
            start_step=str(data["start_step"]),
            steps=steps,
        )


@dataclass(frozen=True, slots=True)
class WorkflowInstance:
    """Runtime state for one minimal workflow execution."""

    workflow_id: str
    process_name: str
    current_step: str


class WorkflowDefinitionLoader:
    """Load JSON workflow definitions from the filesystem."""

    def __init__(self, definitions_dir: Path | None = None) -> None:
        self.definitions_dir = definitions_dir or Path(__file__).parent / "workflows"

    def load_definitions(self) -> dict[str, WorkflowDefinition]:
        """Load all JSON workflow definitions found under the definitions directory."""

        definitions = {}
        for path in sorted(self.definitions_dir.glob("*/*.json")):
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if "process_name" not in data:
                continue
            definition = WorkflowDefinition.from_dict(data)
            definitions[definition.process_name] = definition
        return definitions

    def load_definition(self, path: Path) -> WorkflowDefinition:
        """Load a single workflow definition from a JSON file."""

        with path.open("r", encoding="utf-8") as handle:
            return WorkflowDefinition.from_dict(json.load(handle))
