"""Lightweight draft storage for editable workflow rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
from uuid import uuid4

from .rules import RuleDefinition, _load_yaml_rule

logger = logging.getLogger("client")


@dataclass(frozen=True, slots=True)
class RuleDraftInfo:
    """Metadata for a candidate rule draft stored outside live baseline rules."""

    draft_id: str
    rule_name: str
    created_at: str
    description: str
    filename: str
    path: str
    sha256: str
    state: str = "draft"
    history: list[dict[str, str]] = field(default_factory=list)
    approved_snapshot_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize draft metadata for storage or UI display."""

        return asdict(self)


class RuleDraftManager:
    """Save, list, load, and delete candidate rule drafts."""

    def __init__(self, drafts_root: Path | None = None) -> None:
        self.drafts_root = drafts_root or _default_drafts_root()
        self.drafts_root.mkdir(parents=True, exist_ok=True)

    def save_rule_draft(
        self,
        rule_name: str,
        content: str,
        description: str | None = None,
    ) -> RuleDraftInfo:
        """Validate and save a candidate YAML rule draft."""

        rule_name = _validate_identifier("rule_name", rule_name)
        content = _validate_non_empty("content", content)
        description = (description or "").strip()

        draft_id = uuid4().hex
        rule_dir = self._rule_dir(rule_name)
        rule_dir.mkdir(parents=True, exist_ok=True)
        draft_path = rule_dir / f"{draft_id}.yaml"
        metadata_path = rule_dir / f"{draft_id}.json"

        draft_path.write_text(content, encoding="utf-8")
        try:
            rule = RuleDefinition.from_dict(_load_yaml_rule(draft_path))
            if rule.decision_name != rule_name:
                raise ValueError("Draft decision_name does not match rule_name.")
        except Exception:
            draft_path.unlink(missing_ok=True)
            raise

        info = RuleDraftInfo(
            draft_id=draft_id,
            rule_name=rule_name,
            created_at=datetime.now(UTC).isoformat(),
            description=description,
            filename=draft_path.name,
            path=str(draft_path),
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            state="draft",
            approved_snapshot_id=None,
            history=[
                {
                    "from_state": "",
                    "to_state": "draft",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "note": "Draft created.",
                }
            ],
        )
        self._write_draft_info(info)
        return info

    def create_draft_from_baseline(
        self,
        rule_name: str,
        baseline_path: Path,
        description: str | None = None,
    ) -> RuleDraftInfo:
        """Copy the current baseline rule into a candidate draft."""

        return self.save_rule_draft(
            rule_name=rule_name,
            content=baseline_path.read_text(encoding="utf-8"),
            description=description,
        )

    def list_rule_drafts(self, rule_name: str) -> list[RuleDraftInfo]:
        """List saved drafts for a rule."""

        rule_dir = self._rule_dir(_validate_identifier("rule_name", rule_name))
        drafts = []
        for metadata_path in sorted(rule_dir.glob("*.json")):
            try:
                drafts.append(self._read_draft_info(metadata_path))
            except (OSError, TypeError, KeyError, json.JSONDecodeError):
                logger.exception(
                    "Skipping invalid workflow rule draft metadata: %s",
                    metadata_path,
                )
        return drafts

    def get_rule_draft_info(self, rule_name: str, draft_id: str) -> RuleDraftInfo:
        """Load metadata for one draft."""

        metadata_path = self._metadata_path(rule_name, draft_id)
        if not metadata_path.exists():
            raise FileNotFoundError(metadata_path)
        return self._read_draft_info(metadata_path)

    def update_rule_draft_info(self, info: RuleDraftInfo) -> RuleDraftInfo:
        """Persist updated draft metadata."""

        self._write_draft_info(info)
        return info

    def load_rule_draft(self, rule_name: str, draft_id: str) -> str:
        """Load draft YAML content."""

        draft_path = self._draft_path(rule_name, draft_id)
        return draft_path.read_text(encoding="utf-8")

    def get_draft_path(self, rule_name: str, draft_id: str) -> Path:
        """Return the filesystem path for a stored draft."""

        draft_path = self._draft_path(rule_name, draft_id)
        if not draft_path.exists():
            raise FileNotFoundError(draft_path)
        return draft_path

    def delete_rule_draft(self, rule_name: str, draft_id: str) -> bool:
        """Delete a draft and its metadata if present."""

        draft_path = self._draft_path(rule_name, draft_id)
        metadata_path = self._metadata_path(rule_name, draft_id)
        existed = draft_path.exists() or metadata_path.exists()
        draft_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        return existed

    def _rule_dir(self, rule_name: str) -> Path:
        return self.drafts_root / rule_name

    def _draft_path(self, rule_name: str, draft_id: str) -> Path:
        rule_name = _validate_identifier("rule_name", rule_name)
        draft_id = _validate_identifier("draft_id", draft_id)
        return self._rule_dir(rule_name) / f"{draft_id}.yaml"

    def _metadata_path(self, rule_name: str, draft_id: str) -> Path:
        rule_name = _validate_identifier("rule_name", rule_name)
        draft_id = _validate_identifier("draft_id", draft_id)
        return self._rule_dir(rule_name) / f"{draft_id}.json"

    def _read_draft_info(self, metadata_path: Path) -> RuleDraftInfo:
        with metadata_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        payload.setdefault("state", "draft")
        payload.setdefault("history", [])
        payload.setdefault("approved_snapshot_id", None)
        return RuleDraftInfo(**payload)

    def _write_draft_info(self, info: RuleDraftInfo) -> None:
        metadata_path = self._metadata_path(info.rule_name, info.draft_id)
        temp_path = metadata_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(info.to_dict(), indent=2), encoding="utf-8")
        os.replace(temp_path, metadata_path)


def _validate_non_empty(field_name: str, value: str) -> str:
    """Validate simple draft-manager inputs."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _validate_identifier(field_name: str, value: str) -> str:
    """Validate identifiers before using them in draft filesystem paths."""

    identifier = _validate_non_empty(field_name, value)
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
    if any(character not in allowed for character in identifier):
        raise ValueError(f"{field_name} contains unsupported characters.")
    return identifier


def _default_drafts_root() -> Path:
    """Return the user-data draft folder without importing the full app config."""

    home = Path.home()
    app_name = "LabTycoon"

    if sys.platform == "darwin":
        app_data_dir = home / "Library" / "Application Support" / app_name
    elif sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        app_data_dir = (
            Path(appdata) / app_name
            if appdata
            else home / "AppData" / "Roaming" / app_name
        )
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        app_data_dir = (
            Path(xdg_data_home) / app_name
            if xdg_data_home
            else home / ".local" / "share" / app_name
        )

    return app_data_dir / "workflow_rule_drafts"
