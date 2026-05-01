"""UI-facing helpers for read-only rule preview."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any

from .rule_drafts import RuleDraftManager
from .rules import RuleEngine
from .rule_lifecycle import RuleDraftLifecycleService
from .rule_promotion import RulePromotionService
from .rule_preview import RulePreviewService
from .rule_snapshots import RulePreviewSnapshotService

DEFAULT_RULE_NAME = "possible_duplicate_order"
DEFAULT_CONTEXT_JSON = json.dumps(
    {
        "payload": {
            "order_id": "PREVIEW-001",
            "patient_id": "P001",
            "test_code": "CBC",
            "duplicate_candidate": True,
        }
    },
    indent=2,
)
DEFAULT_RULE_PATH = (
    Path(__file__).parent
    / "workflows"
    / "rules"
    / "possible_duplicate_order.yaml"
)


def load_default_candidate_rule_text() -> str:
    """Return the current editable rule text for the preview UI."""

    return DEFAULT_RULE_PATH.read_text(encoding="utf-8")


def preview_rule_text(
    rule_name: str,
    context_json: str,
    candidate_rule_text: str = "",
    service: RulePreviewService | None = None,
) -> dict[str, Any]:
    """Run a read-only rule preview from UI text inputs.

    This helper is intentionally independent of FreeSimpleGUI/Orange so it can
    be tested without launching a UI. It never writes candidate rules to a live
    workflow location and never calls the action adapter.
    """

    try:
        context = json.loads(context_json)
        if not isinstance(context, dict):
            raise ValueError("Context JSON must decode to an object.")
    except (json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "error": f"Invalid context JSON: {exc}"}

    preview_service = service or RulePreviewService()
    candidate_text = candidate_rule_text.strip()

    try:
        if not candidate_text:
            preview = preview_service.preview_rule_change(rule_name, context)
            return {"ok": True, "preview": preview.to_dict()}

        with tempfile.TemporaryDirectory() as tmpdir:
            candidate_path = Path(tmpdir) / f"{rule_name}.yaml"
            candidate_path.write_text(candidate_text, encoding="utf-8")
            preview = preview_service.preview_rule_change(
                rule_name,
                context,
                candidate_rule_path=candidate_path,
            )
            return {"ok": True, "preview": preview.to_dict()}
    except Exception as exc:
        return {"ok": False, "error": f"Preview failed: {exc}"}


def build_order_preview_context_json(
    order_id: int | str,
    window_hours: int = 4,
    context_builder=None,
) -> dict[str, Any]:
    """Build pretty JSON preview context from a stored LabTycoon order."""

    try:
        if context_builder is None:
            from app.system.signals.repository import build_rule_context_for_order_id

            context_builder = build_rule_context_for_order_id

        context = context_builder(order_id, window_hours=window_hours)
        return {"ok": True, "context": context, "context_json": json.dumps(context, indent=2)}
    except Exception as exc:
        return {"ok": False, "error": f"Could not build order context: {exc}"}


def list_orders_flagged_by_current_rule(
    rule_name: str = DEFAULT_RULE_NAME,
    window_hours: int = 4,
    context_row_builder=None,
    rule_engine: RuleEngine | None = None,
) -> dict[str, Any]:
    """Return stored orders whose generated context matches the current rule."""

    try:
        if context_row_builder is None:
            from app.system.signals.repository import build_rule_context_rows_for_orders

            context_row_builder = build_rule_context_rows_for_orders

        engine = rule_engine or RuleEngine()
        flagged_orders = []
        for row in context_row_builder(window_hours=window_hours):
            decision = engine.evaluate(rule_name, row["context"])
            if not decision.matched:
                continue
            payload = row["context"].get("payload", {})
            flagged_orders.append(
                {
                    "order_id": row["order_id"],
                    "hn": row["hn"],
                    "patient_name": row["patient_name"],
                    "ordered_at": row["ordered_at"],
                    "priority": row["priority"],
                    "test_code": row["test_code"],
                    "reason": decision.reason,
                    "duplicate_candidate": bool(payload.get("duplicate_candidate")),
                    "matched_fields": decision.metadata.get("matched_fields", []),
                }
            )
        return {"ok": True, "orders": flagged_orders}
    except Exception as exc:
        return {"ok": False, "error": f"Could not list flagged orders: {exc}"}


def build_stewardship_ai_summary(order: dict[str, Any]) -> str:
    """Return a simulated AI stewardship explanation for a flagged order."""

    order_id = order.get("order_id", "")
    patient_name = order.get("patient_name", "")
    hn = order.get("hn", "")
    test_code = order.get("test_code") or "the ordered test"
    ordered_at = order.get("ordered_at", "")
    priority = order.get("priority", "")
    reason = order.get("reason", "The current rule flagged this order.")
    matched_fields = ", ".join(order.get("matched_fields", [])) or "current rule conditions"

    return (
        "Simulated AI stewardship summary\n\n"
        f"Order {order_id} for {patient_name} (HN {hn}) was flagged by the current duplicate-order rule. "
        f"The rule matched {matched_fields}. The key signal is that {test_code} appears to repeat for "
        "the same patient within the configured duplicate-review window. "
        f"Order priority is {priority or 'not specified'} and the order time is {ordered_at or 'not recorded'}.\n\n"
        f"Rule reason: {reason}\n\n"
        "Recommended staff action\n"
        "1. Check whether this is an intentional repeat, add-on, correction, or separate clinical episode.\n"
        "2. Compare the current order with recent orders for the same patient and test.\n"
        "3. Confirm with the ward or requesting clinician before cancelling or merging anything.\n"
        "4. If it is a true duplicate, follow the local SOP for cancellation, documentation, and communication.\n"
        "5. If it is clinically valid, document the reason and continue normal processing."
    )


def save_rule_draft_text(
    rule_name: str,
    candidate_rule_text: str,
    description: str = "",
    draft_manager: RuleDraftManager | None = None,
) -> dict[str, Any]:
    """Validate and save candidate YAML text as a non-live draft."""

    manager = draft_manager or RuleDraftManager()
    try:
        draft = manager.save_rule_draft(rule_name, candidate_rule_text, description)
    except Exception as exc:
        return {"ok": False, "error": f"Could not save draft: {exc}"}
    return {"ok": True, "draft": draft.to_dict()}


def list_rule_drafts(
    rule_name: str,
    draft_manager: RuleDraftManager | None = None,
) -> list[dict[str, Any]]:
    """List non-live candidate drafts for a rule."""

    manager = draft_manager or RuleDraftManager()
    return [draft.to_dict() for draft in manager.list_rule_drafts(rule_name)]


def load_rule_draft_text(
    rule_name: str,
    draft_id: str,
    draft_manager: RuleDraftManager | None = None,
) -> dict[str, Any]:
    """Load draft YAML text for editing or preview."""

    manager = draft_manager or RuleDraftManager()
    try:
        return {"ok": True, "content": manager.load_rule_draft(rule_name, draft_id)}
    except Exception as exc:
        return {"ok": False, "error": f"Could not load draft: {exc}"}


def preview_rule_draft(
    rule_name: str,
    context_json: str,
    draft_id: str,
    draft_manager: RuleDraftManager | None = None,
    service: RulePreviewService | None = None,
) -> dict[str, Any]:
    """Preview baseline rule behavior against a stored candidate draft."""

    manager = draft_manager or RuleDraftManager()
    try:
        draft_path = manager.get_draft_path(rule_name, draft_id)
    except Exception as exc:
        return {"ok": False, "error": f"Could not find draft: {exc}"}

    try:
        context = json.loads(context_json)
        if not isinstance(context, dict):
            raise ValueError("Context JSON must decode to an object.")
    except (json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "error": f"Invalid context JSON: {exc}"}

    try:
        preview = (service or RulePreviewService()).preview_rule_change(
            rule_name,
            context,
            candidate_rule_path=draft_path,
        )
    except Exception as exc:
        return {"ok": False, "error": f"Preview failed: {exc}"}

    return {"ok": True, "preview": preview.to_dict()}


def promote_rule_draft(
    rule_name: str,
    draft_id: str,
    confirmation: bool = False,
    note: str = "",
    promotion_service: RulePromotionService | None = None,
) -> dict[str, Any]:
    """Promote a saved draft to baseline through the guarded lifecycle service."""

    service = promotion_service or RulePromotionService()
    result = service.promote_rule_draft(
        rule_name=rule_name,
        draft_id=draft_id,
        confirmation=confirmation,
        note=note,
    )
    return result.to_dict()


def transition_rule_draft(
    rule_name: str,
    draft_id: str,
    target_state: str,
    note: str = "",
    snapshot_id: str | None = None,
    lifecycle_service: RuleDraftLifecycleService | None = None,
) -> dict[str, Any]:
    """Apply a safe draft lifecycle transition from the UI layer."""

    service = lifecycle_service or RuleDraftLifecycleService()
    result = service.transition_rule_draft(
        rule_name=rule_name,
        draft_id=draft_id,
        target_state=target_state,
        note=note,
        snapshot_id=snapshot_id,
    )
    return result.to_dict()


def create_preview_snapshot(
    rule_name: str,
    draft_id: str,
    context_json: str,
    note: str = "",
    snapshot_service: RulePreviewSnapshotService | None = None,
) -> dict[str, Any]:
    """Persist a read-only preview result as approval evidence."""

    try:
        context = json.loads(context_json)
        if not isinstance(context, dict):
            raise ValueError("Context JSON must decode to an object.")
    except (json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "error": f"Invalid context JSON: {exc}"}

    service = snapshot_service or RulePreviewSnapshotService()
    try:
        snapshot = service.create_preview_snapshot(rule_name, draft_id, context, note)
    except Exception as exc:
        return {"ok": False, "error": f"Could not create snapshot: {exc}"}
    return {"ok": True, "snapshot": snapshot.to_dict()}


def list_preview_snapshots(
    rule_name: str,
    draft_id: str | None = None,
    snapshot_service: RulePreviewSnapshotService | None = None,
) -> list[dict[str, Any]]:
    """List saved preview evidence snapshots."""

    service = snapshot_service or RulePreviewSnapshotService()
    return [
        snapshot.to_dict()
        for snapshot in service.list_rule_snapshots(rule_name, draft_id)
    ]


def load_preview_snapshot(
    snapshot_id: str,
    snapshot_service: RulePreviewSnapshotService | None = None,
) -> dict[str, Any]:
    """Load one saved preview snapshot by id."""

    service = snapshot_service or RulePreviewSnapshotService()
    try:
        return {"ok": True, "snapshot": service.load_rule_snapshot(snapshot_id).to_dict()}
    except Exception as exc:
        return {"ok": False, "error": f"Could not load snapshot: {exc}"}
