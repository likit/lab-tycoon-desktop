"""Guarded promotion of candidate rule drafts to active baseline rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .rule_drafts import RuleDraftManager
from .rule_lifecycle import RULE_APPROVED, RuleDraftLifecycleService
from .rules import RuleDefinition, _load_yaml_rule


@dataclass(frozen=True, slots=True)
class RulePromotionResult:
    """Structured result returned by a draft promotion attempt."""

    ok: bool
    rule_name: str
    draft_id: str
    message: str
    baseline_path: str | None = None
    backup_path: str | None = None
    history_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize promotion result for UI display and tests."""

        return asdict(self)


class RulePromotionService:
    """Promote validated candidate drafts into the active baseline rule folder.

    Promotion is intentionally separate from preview and live workflow execution.
    The caller must pass ``confirmation=True`` to make the baseline replacement
    explicit.
    """

    def __init__(
        self,
        draft_manager: RuleDraftManager | None = None,
        lifecycle_service: RuleDraftLifecycleService | None = None,
        baseline_rules_dir: Path | None = None,
        history_dir: Path | None = None,
    ) -> None:
        self.draft_manager = draft_manager or RuleDraftManager()
        self.lifecycle_service = lifecycle_service or RuleDraftLifecycleService(
            draft_manager=self.draft_manager
        )
        self.baseline_rules_dir = baseline_rules_dir or (
            Path(__file__).parent / "workflows" / "rules"
        )
        self.history_dir = history_dir or _default_history_dir(self.draft_manager)

    def promote_rule_draft(
        self,
        rule_name: str,
        draft_id: str,
        confirmation: bool = False,
        note: str | None = None,
    ) -> RulePromotionResult:
        """Validate and promote one saved draft to the active baseline rule."""

        if not confirmation:
            return RulePromotionResult(
                ok=False,
                rule_name=rule_name,
                draft_id=draft_id,
                message="Promotion requires explicit confirmation.",
                error="confirmation_required",
            )

        cleanup_paths: list[Path] = []
        try:
            draft_info = self.draft_manager.get_rule_draft_info(rule_name, draft_id)
            if draft_info.state != RULE_APPROVED:
                return RulePromotionResult(
                    ok=False,
                    rule_name=rule_name,
                    draft_id=draft_id,
                    message="Promotion requires an approved draft.",
                    error="approval_required",
                )

            draft_path = self.draft_manager.get_draft_path(rule_name, draft_id)
            draft_content = draft_path.read_text(encoding="utf-8")
            rule = RuleDefinition.from_dict(_load_yaml_rule(draft_path))
            if rule.decision_name != rule_name:
                raise ValueError("Draft decision_name does not match rule_name.")

            baseline_path = self._baseline_path(rule_name)
            if not baseline_path.exists():
                raise FileNotFoundError(baseline_path)

            baseline_content = baseline_path.read_text(encoding="utf-8")
            promoted_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            rule_history_dir = self.history_dir / rule_name
            rule_history_dir.mkdir(parents=True, exist_ok=True)
            backup_path = rule_history_dir / f"{promoted_at}_{draft_id}.baseline.yaml"
            history_path = rule_history_dir / f"{promoted_at}_{draft_id}.promotion.json"
            temp_baseline_path = baseline_path.with_name(f".{baseline_path.name}.promote.tmp")
            temp_history_path = history_path.with_suffix(".json.tmp")
            cleanup_paths.extend([temp_baseline_path, temp_history_path, history_path])

            backup_path.write_text(baseline_content, encoding="utf-8")
            history_record = {
                "rule_name": rule_name,
                "draft_id": draft_id,
                "promoted_at": datetime.now(UTC).isoformat(),
                "note": note or "",
                "baseline_path": str(baseline_path),
                "backup_path": str(backup_path),
                "draft_path": str(draft_path),
                "approved_snapshot_id": draft_info.approved_snapshot_id,
                "previous_baseline_sha256": hashlib.sha256(
                    baseline_content.encode("utf-8")
                ).hexdigest(),
                "promoted_draft_sha256": hashlib.sha256(
                    draft_content.encode("utf-8")
                ).hexdigest(),
            }
            temp_history_path.write_text(
                json.dumps(history_record, indent=2),
                encoding="utf-8",
            )

            temp_baseline_path.write_text(draft_content, encoding="utf-8")
            os.replace(temp_history_path, history_path)
            os.replace(temp_baseline_path, baseline_path)
            self.lifecycle_service.mark_promoted(
                rule_name,
                draft_id,
                note="Draft promoted to baseline.",
            )

            return RulePromotionResult(
                ok=True,
                rule_name=rule_name,
                draft_id=draft_id,
                message="Draft promoted to baseline.",
                baseline_path=str(baseline_path),
                backup_path=str(backup_path),
                history_path=str(history_path),
            )
        except Exception as exc:
            for cleanup_path in cleanup_paths:
                cleanup_path.unlink(missing_ok=True)
            return RulePromotionResult(
                ok=False,
                rule_name=rule_name,
                draft_id=draft_id,
                message=f"Promotion failed: {exc}",
                error=exc.__class__.__name__,
            )

    def _baseline_path(self, rule_name: str) -> Path:
        """Return the active baseline artifact path for a rule."""

        return self.baseline_rules_dir / f"{rule_name}.yaml"


def _default_history_dir(draft_manager: RuleDraftManager) -> Path:
    """Store promotion history in app data alongside other workflow artifacts."""

    return draft_manager.drafts_root.parent / "workflow_rule_history"
