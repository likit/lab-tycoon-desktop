"""Small approval state machine for candidate workflow rule drafts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from typing import Any

from .rule_drafts import RuleDraftInfo, RuleDraftManager
from .rule_snapshots import RulePreviewSnapshotService

RULE_DRAFT = "draft"
RULE_REVIEWED = "reviewed"
RULE_APPROVED = "approved"
RULE_PROMOTED = "promoted"

ALLOWED_TRANSITIONS = {
    RULE_DRAFT: {RULE_REVIEWED},
    RULE_REVIEWED: {RULE_APPROVED},
    RULE_APPROVED: {RULE_PROMOTED},
    RULE_PROMOTED: set(),
}


@dataclass(frozen=True, slots=True)
class RuleDraftTransitionResult:
    """Structured result returned by a draft lifecycle transition."""

    ok: bool
    rule_name: str
    draft_id: str
    old_state: str | None
    new_state: str | None
    message: str
    draft: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize transition result for UI display and tests."""

        return asdict(self)


class RuleDraftLifecycleService:
    """Apply explicit state transitions to candidate rule drafts."""

    def __init__(
        self,
        draft_manager: RuleDraftManager | None = None,
        snapshot_service: RulePreviewSnapshotService | None = None,
    ) -> None:
        self.draft_manager = draft_manager or RuleDraftManager()
        self.snapshot_service = snapshot_service or RulePreviewSnapshotService(
            draft_manager=self.draft_manager
        )

    def transition_rule_draft(
        self,
        rule_name: str,
        draft_id: str,
        target_state: str,
        note: str | None = None,
        snapshot_id: str | None = None,
    ) -> RuleDraftTransitionResult:
        """Move a draft to a valid next state and append audit history."""

        try:
            draft = self.draft_manager.get_rule_draft_info(rule_name, draft_id)
            target_state = target_state.strip().lower()
            if target_state not in ALLOWED_TRANSITIONS:
                return _transition_error(
                    draft,
                    target_state,
                    "invalid_state",
                    f"Unknown draft lifecycle state: {target_state}.",
                )
            if target_state not in ALLOWED_TRANSITIONS.get(draft.state, set()):
                return _transition_error(
                    draft,
                    target_state,
                    "invalid_transition",
                    f"Cannot transition draft from {draft.state} to {target_state}.",
                )
            if target_state == RULE_APPROVED and snapshot_id:
                try:
                    snapshot = self.snapshot_service.load_rule_snapshot(snapshot_id)
                except FileNotFoundError:
                    return _transition_error(
                        draft,
                        target_state,
                        "snapshot_missing",
                        "Approval snapshot was not found.",
                    )
                except (OSError, TypeError, KeyError, json.JSONDecodeError):
                    return _transition_error(
                        draft,
                        target_state,
                        "snapshot_invalid",
                        "Approval snapshot could not be loaded.",
                    )
                if snapshot.rule_name != rule_name or snapshot.draft_id != draft_id:
                    return _transition_error(
                        draft,
                        target_state,
                        "snapshot_mismatch",
                        "Approval snapshot does not belong to this draft.",
                    )

            updated = RuleDraftInfo(
                draft_id=draft.draft_id,
                rule_name=draft.rule_name,
                created_at=draft.created_at,
                description=draft.description,
                filename=draft.filename,
                path=draft.path,
                sha256=draft.sha256,
                state=target_state,
                approved_snapshot_id=(
                    snapshot_id
                    if target_state == RULE_APPROVED and snapshot_id
                    else draft.approved_snapshot_id
                ),
                history=[
                    *draft.history,
                    {
                        "from_state": draft.state,
                        "to_state": target_state,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "note": note or "",
                        "snapshot_id": snapshot_id or "",
                    },
                ],
            )
            self.draft_manager.update_rule_draft_info(updated)
            return RuleDraftTransitionResult(
                ok=True,
                rule_name=rule_name,
                draft_id=draft_id,
                old_state=draft.state,
                new_state=target_state,
                message=f"Draft moved from {draft.state} to {target_state}.",
                draft=updated.to_dict(),
            )
        except Exception as exc:
            return RuleDraftTransitionResult(
                ok=False,
                rule_name=rule_name,
                draft_id=draft_id,
                old_state=None,
                new_state=target_state,
                message=f"Draft lifecycle transition failed: {exc}",
                error=exc.__class__.__name__,
            )

    def mark_reviewed(
        self,
        rule_name: str,
        draft_id: str,
        note: str | None = None,
    ) -> RuleDraftTransitionResult:
        """Move a draft from draft to reviewed."""

        return self.transition_rule_draft(rule_name, draft_id, RULE_REVIEWED, note)

    def approve_draft(
        self,
        rule_name: str,
        draft_id: str,
        note: str | None = None,
        snapshot_id: str | None = None,
    ) -> RuleDraftTransitionResult:
        """Move a draft from reviewed to approved."""

        return self.transition_rule_draft(
            rule_name,
            draft_id,
            RULE_APPROVED,
            note,
            snapshot_id=snapshot_id,
        )

    def mark_promoted(
        self,
        rule_name: str,
        draft_id: str,
        note: str | None = None,
    ) -> RuleDraftTransitionResult:
        """Move an approved draft to promoted after baseline activation."""

        return self.transition_rule_draft(rule_name, draft_id, RULE_PROMOTED, note)


def _transition_error(
    draft: RuleDraftInfo,
    target_state: str,
    error: str,
    message: str,
) -> RuleDraftTransitionResult:
    """Build a failed transition result without mutating metadata."""

    return RuleDraftTransitionResult(
        ok=False,
        rule_name=draft.rule_name,
        draft_id=draft.draft_id,
        old_state=draft.state,
        new_state=target_state,
        message=message,
        draft=draft.to_dict(),
        error=error,
    )
