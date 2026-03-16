"""JSON persistence for workflow documents."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

from ..model import Workflow


@dataclass(slots=True)
class WorkflowDocument:
    """A named workflow asset that can be stored and reopened later."""

    workflow_id: str
    name: str
    workflow: Workflow

    def to_dict(self) -> dict[str, Any]:
        """Serialize the document and its workflow payload."""

        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "workflow": self.workflow.to_dict(),
        }

    @classmethod
    def create(cls, name: str, workflow: Workflow | None = None) -> "WorkflowDocument":
        """Create a new workflow document with a generated identifier."""

        return cls(
            workflow_id=uuid4().hex,
            name=name,
            workflow=workflow or Workflow(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowDocument":
        """Rehydrate a workflow document from stored JSON data."""

        return cls(
            workflow_id=str(data.get("workflow_id", uuid4().hex)),
            name=str(data.get("name", "Untitled Workflow")),
            workflow=Workflow.from_dict(dict(data.get("workflow", {}))),
        )


class WorkflowRepository:
    """Store workflow documents as JSON files on disk."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else self.default_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def default_base_dir(app_name: str = "LabTycoon") -> Path:
        """Return a writable user-data directory for workflow documents.

        This avoids saving inside the source tree or a PyInstaller bundle,
        which may be read-only or temporary in packaged builds.
        """

        home = Path.home()

        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / app_name / "workflows"

        if sys.platform.startswith("win"):
            appdata = os.environ.get("APPDATA")
            if appdata:
                return Path(appdata) / app_name / "workflows"
            return home / "AppData" / "Roaming" / app_name / "workflows"

        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if xdg_data_home:
            return Path(xdg_data_home) / app_name / "workflows"
        return home / ".local" / "share" / app_name / "workflows"

    def list_documents(self) -> list[WorkflowDocument]:
        """Load every saved workflow document from the repository."""

        documents = []
        for path in sorted(self.base_dir.glob("*.json")):
            try:
                documents.append(self.load(path.stem))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return documents

    def load(self, workflow_id: str) -> WorkflowDocument:
        """Load a single workflow document by identifier."""

        path = self.base_dir / f"{workflow_id}.json"
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return WorkflowDocument.from_dict(payload)

    def save(self, document: WorkflowDocument) -> Path:
        """Persist a workflow document and return the written file path."""

        path = self.base_dir / f"{document.workflow_id}.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(document.to_dict(), handle, indent=2)
        return path
