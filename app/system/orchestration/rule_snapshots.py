"""Immutable preview snapshots used as approval evidence for rule drafts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from .rule_drafts import RuleDraftManager
from .rule_preview import RulePreviewService

logger = logging.getLogger("client")


@dataclass(frozen=True, slots=True)
class RulePreviewSnapshotInfo:
    """Metadata and content for one saved rule-preview evidence package."""

    snapshot_id: str
    rule_name: str
    draft_id: str
    created_at: str
    note: str
    baseline_reference: str
    candidate_reference: str
    context: dict[str, Any]
    preview: dict[str, Any]
    sha256: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize snapshot metadata and preview evidence."""

        return asdict(self)


class RulePreviewSnapshotService:
    """Create, list, and load immutable preview evidence snapshots."""

    def __init__(
        self,
        draft_manager: RuleDraftManager | None = None,
        preview_service: RulePreviewService | None = None,
        snapshots_root: Path | None = None,
    ) -> None:
        self.draft_manager = draft_manager or RuleDraftManager()
        self.preview_service = preview_service or RulePreviewService()
        self.snapshots_root = snapshots_root or self.draft_manager.drafts_root / "snapshots"
        self.snapshots_root.mkdir(parents=True, exist_ok=True)

    def create_preview_snapshot(
        self,
        rule_name: str,
        draft_id: str,
        context: dict[str, Any],
        note: str | None = None,
    ) -> RulePreviewSnapshotInfo:
        """Persist the exact baseline-vs-draft preview result as JSON evidence."""

        if not isinstance(context, dict):
            raise ValueError("context must be a dictionary.")

        draft_path = self.draft_manager.get_draft_path(rule_name, draft_id)
        preview = self.preview_service.preview_rule_change(
            rule_name,
            context,
            candidate_rule_path=draft_path,
        )
        snapshot_id = uuid4().hex
        snapshot_path = self._snapshot_path(rule_name, draft_id, snapshot_id)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        if snapshot_path.exists():
            raise FileExistsError(snapshot_path)

        preview_payload = preview.to_dict()
        content_for_hash = json.dumps(
            {
                "rule_name": rule_name,
                "draft_id": draft_id,
                "context": context,
                "preview": preview_payload,
            },
            sort_keys=True,
        )
        info = RulePreviewSnapshotInfo(
            snapshot_id=snapshot_id,
            rule_name=rule_name,
            draft_id=draft_id,
            created_at=datetime.now(UTC).isoformat(),
            note=note or "",
            baseline_reference=f"{rule_name}.yaml",
            candidate_reference=str(draft_path),
            context=context,
            preview=preview_payload,
            sha256=hashlib.sha256(content_for_hash.encode("utf-8")).hexdigest(),
            path=str(snapshot_path),
        )
        snapshot_path.write_text(json.dumps(info.to_dict(), indent=2), encoding="utf-8")
        return info

    def list_rule_snapshots(
        self,
        rule_name: str,
        draft_id: str | None = None,
    ) -> list[RulePreviewSnapshotInfo]:
        """List saved preview snapshots for a rule or one draft."""

        search_root = self.snapshots_root / rule_name
        pattern = f"{draft_id}/*.json" if draft_id else "*/*.json"
        snapshots = []
        for path in sorted(search_root.glob(pattern)):
            if not path.is_file():
                continue
            try:
                snapshots.append(self._read_snapshot(path))
            except (OSError, TypeError, KeyError, json.JSONDecodeError):
                logger.exception("Skipping invalid rule preview snapshot: %s", path)
        return snapshots

    def load_rule_snapshot(self, snapshot_id: str) -> RulePreviewSnapshotInfo:
        """Load one snapshot by id."""

        for path in self.snapshots_root.glob(f"*/*/{snapshot_id}.json"):
            return self._read_snapshot(path)
        raise FileNotFoundError(snapshot_id)

    def _snapshot_path(self, rule_name: str, draft_id: str, snapshot_id: str) -> Path:
        return self.snapshots_root / rule_name / draft_id / f"{snapshot_id}.json"

    def _read_snapshot(self, path: Path) -> RulePreviewSnapshotInfo:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return RulePreviewSnapshotInfo(**payload)
