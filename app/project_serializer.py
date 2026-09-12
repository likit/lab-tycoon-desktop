"""Read and write validated Lab Tycoon Project (.ltp) archives."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.project import (
    APPLICATION_VERSION,
    PROJECT_FORMAT_VERSION,
    LabTycoonProject,
    ProjectFloor,
)
from app.system.model import Workflow


PROJECT_FORMAT = "labtycoon-project"
REQUIRED_MEMBERS = {
    "manifest.json",
    "laboratory/floor.json",
    "laboratory/equipment.json",
    "workflows/main.json",
}


class ProjectFormatError(ValueError):
    """Raised when an .ltp archive is missing or contains invalid project data."""


class ProjectSerializer:
    """Serialize projects independently of all GUI views."""

    @staticmethod
    def ensure_extension(path: str | Path) -> Path:
        output_path = Path(path)
        return output_path if output_path.suffix.lower() == ".ltp" else output_path.with_suffix(".ltp")

    def save(self, project: LabTycoonProject, path: str | Path) -> Path:
        output_path = self.ensure_extension(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        project.modified_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "format": PROJECT_FORMAT,
            "format_version": project.format_version,
            "project_id": project.project_id,
            "name": project.name,
            "description": project.description,
            "created_at": project.created_at,
            "modified_at": project.modified_at,
            "application_version": APPLICATION_VERSION,
        }

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
                self._write_json(archive, "manifest.json", manifest)
                self._write_json(archive, "laboratory/floor.json", project.floor.floor_dict())
                self._write_json(archive, "laboratory/equipment.json", project.floor.equipment_dict())
                self._write_json(archive, "workflows/main.json", project.workflow.to_dict())
            self._validate_archive(temporary_path)
            os.replace(temporary_path, output_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

        project.file_path = output_path
        project.is_dirty = False
        return output_path

    def load(self, path: str | Path) -> LabTycoonProject:
        input_path = Path(path)
        try:
            with ZipFile(input_path, "r") as archive:
                self._validate_members(archive)
                manifest = self._read_json(archive, "manifest.json")
                self._validate_manifest(manifest)
                floor_data = self._read_json(archive, "laboratory/floor.json")
                equipment_data = self._read_json(archive, "laboratory/equipment.json")
                workflow_data = self._read_json(archive, "workflows/main.json")
        except (BadZipFile, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ProjectFormatError):
                raise
            raise ProjectFormatError(f"Unable to read Lab Tycoon project: {exc}") from exc

        try:
            return LabTycoonProject(
                project_id=str(manifest["project_id"]),
                name=str(manifest["name"]),
                description=str(manifest.get("description", "")),
                created_at=str(manifest["created_at"]),
                modified_at=str(manifest["modified_at"]),
                format_version=str(manifest["format_version"]),
                floor=ProjectFloor.from_dicts(floor_data, equipment_data),
                workflow=Workflow.from_dict(workflow_data),
                is_dirty=False,
                file_path=input_path,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectFormatError(f"Project data is invalid: {exc}") from exc

    def _validate_archive(self, path: Path) -> None:
        try:
            with ZipFile(path, "r") as archive:
                self._validate_members(archive)
                self._validate_manifest(self._read_json(archive, "manifest.json"))
                if archive.testzip() is not None:
                    raise ProjectFormatError("Project archive contains corrupt data.")
        except BadZipFile as exc:
            raise ProjectFormatError("Project is not a readable ZIP archive.") from exc

    @staticmethod
    def _validate_members(archive: ZipFile) -> None:
        missing = REQUIRED_MEMBERS.difference(archive.namelist())
        if missing:
            raise ProjectFormatError(f"Project archive is missing: {', '.join(sorted(missing))}")

    @staticmethod
    def _validate_manifest(manifest: dict[str, object]) -> None:
        if manifest.get("format") != PROJECT_FORMAT:
            raise ProjectFormatError("Invalid Lab Tycoon project manifest format.")
        if manifest.get("format_version") != PROJECT_FORMAT_VERSION:
            raise ProjectFormatError(
                f"Unsupported Lab Tycoon project format version: {manifest.get('format_version')}"
            )
        for key in ("project_id", "name", "created_at", "modified_at"):
            if not manifest.get(key):
                raise ProjectFormatError(f"Project manifest is missing {key}.")

    @staticmethod
    def _write_json(archive: ZipFile, member: str, payload: object) -> None:
        archive.writestr(member, json.dumps(payload, indent=2, sort_keys=True))

    @staticmethod
    def _read_json(archive: ZipFile, member: str) -> dict[str, object]:
        payload = json.loads(archive.read(member).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ProjectFormatError(f"{member} must contain a JSON object.")
        return payload
