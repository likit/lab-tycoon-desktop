"""Governance boundary for workflow-driven simulation decisions.

This module adapts existing SOP-as-Code logic into a narrow governance layer
that workflow nodes can call without knowing anything about the underlying
rule engine or review workflow implementation.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from app.system.orchestration.rules import DecisionResult, RuleEngine
from app.system.signals.context_builder import build_rule_context


EVENT_ORDER_RECEIVED = "ORDER_RECEIVED"
EVENT_DUPLICATE_ORDER_CHECK = "DUPLICATE_ORDER_CHECK"
EVENT_DUPLICATE_ORDER_DETECTED = "duplicate_order_detected"

ACTION_ALLOW_ORDER = "ALLOW_ORDER"
ACTION_REJECT_DUPLICATE = "REJECT_DUPLICATE"
ACTION_MERGE_ORDER = "MERGE_ORDER"
ACTION_REQUEST_REVIEW = "REQUEST_REVIEW"
ACTION_CONTINUE = "CONTINUE"

DEFAULT_DUPLICATE_RULE_NAME = "possible_duplicate_order"


@dataclass(frozen=True, slots=True)
class GovernanceEvent:
    """One governance consultation emitted by a workflow node."""

    event_type: str
    node_id: str
    order_id: str | None = None
    specimen_id: str | None = None
    sim_time: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event for logs and tests."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class Decision:
    """Standardized governance decision returned to the simulation engine."""

    action: str
    requires_human: bool
    reason: str
    target_node: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the decision for logs and tests."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class GovernanceConsultation:
    """Log entry created every time a workflow node consults governance."""

    sim_time: float
    event_type: str
    node_id: str
    order_id: str | None
    specimen_id: str | None
    provider_name: str
    decision_action: str
    requires_human: bool
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the consultation for simulation results and CLI output."""

        return {
            "sim_time": self.sim_time,
            "event_type": self.event_type,
            "node_id": self.node_id,
            "order_id": self.order_id,
            "specimen_id": self.specimen_id,
            "provider": self.provider_name,
            "provider_name": self.provider_name,
            "decision_action": self.decision_action,
            "requires_human": self.requires_human,
            "reason": self.reason,
            "metadata": deepcopy(self.metadata),
        }


class GovernanceProvider(ABC):
    """Abstract governance decision provider."""

    provider_name = "governance"

    @abstractmethod
    def evaluate(self, event: GovernanceEvent) -> Decision:
        """Evaluate one governance event and return a standardized decision."""


class SopAsCodeGovernanceProvider(GovernanceProvider):
    """Adapt workflow governance events to the existing SOP-as-Code machinery."""

    provider_name = "sop-as-code"

    def __init__(self, rule_engine: RuleEngine | None = None) -> None:
        self.rule_engine = rule_engine or RuleEngine()

    def evaluate(self, event: GovernanceEvent) -> Decision:
        """Evaluate a governance event through the existing SOP rule layer."""

        human_review_status = _normalize_text(event.context.get("human_review_status"))
        if human_review_status is not None:
            return self._decision_from_review_status(event, human_review_status)

        if event.event_type not in {
            EVENT_ORDER_RECEIVED,
            EVENT_DUPLICATE_ORDER_CHECK,
            EVENT_DUPLICATE_ORDER_DETECTED,
        }:
            return Decision(
                action=ACTION_CONTINUE,
                requires_human=False,
                reason="No governance rule matched this event type.",
                target_node=_normalize_text(event.context.get("target_node")),
                metadata={
                    "event_type": event.event_type,
                    "provider": self.provider_name,
                },
            )

        rule_context = self._build_rule_context(event)
        rule_result = self.rule_engine.evaluate(DEFAULT_DUPLICATE_RULE_NAME, rule_context)
        action = self._action_for_event(event, rule_result)
        target_node = _normalize_text(event.context.get("target_node"))

        return Decision(
            action=action,
            requires_human=rule_result.matched,
            reason=rule_result.reason,
            target_node=target_node,
            metadata={
                "provider": self.provider_name,
                "rule_name": DEFAULT_DUPLICATE_RULE_NAME,
                "rule_result": rule_result.to_dict(),
                "rule_context": _summarize_rule_context(rule_context),
                "event_context": _summarize_event_context(event.context),
            },
        )

    def _build_rule_context(self, event: GovernanceEvent) -> dict[str, Any]:
        """Reuse the existing duplicate-order signal builder when possible."""

        order = event.context.get("order")
        prior_orders = event.context.get("prior_orders", [])
        if order is not None and isinstance(prior_orders, list):
            try:
                return build_rule_context(order, prior_orders=prior_orders)
            except Exception:
                pass

        payload = event.context.get("payload")
        if isinstance(payload, dict):
            return {"payload": deepcopy(payload)}

        return {
            "payload": {
                "order_id": event.order_id,
                "patient_id": event.context.get("patient_id"),
                "test_code": event.context.get("test_code"),
                "duplicate_candidate": bool(event.context.get("duplicate_candidate", False)),
                "repeated_indicator": bool(event.context.get("repeated_indicator", False)),
                "possible_duplicate": bool(event.context.get("possible_duplicate", False)),
            }
        }

    def _action_for_event(self, event: GovernanceEvent, rule_result: DecisionResult) -> str:
        """Map the SOP result to a standardized governance action."""

        if not rule_result.matched:
            if event.event_type == EVENT_ORDER_RECEIVED:
                return ACTION_ALLOW_ORDER
            return ACTION_CONTINUE

        requested_action = _normalize_text(event.context.get("matched_action"))
        if requested_action is not None:
            requested_action = requested_action.upper()
        if requested_action in {ACTION_REQUEST_REVIEW, ACTION_REJECT_DUPLICATE, ACTION_MERGE_ORDER}:
            return requested_action
        return ACTION_REQUEST_REVIEW

    def _decision_from_review_status(self, event: GovernanceEvent, review_status: str) -> Decision:
        """Convert an existing review status into a standardized decision."""

        review_status = review_status.lower()

        if review_status in {"approved", "allowed", "allow"}:
            action = ACTION_ALLOW_ORDER
            requires_human = False
        elif review_status in {"merged", "merge"}:
            action = ACTION_MERGE_ORDER
            requires_human = False
        elif review_status in {"rejected", "duplicate", "reject"}:
            action = ACTION_REJECT_DUPLICATE
            requires_human = False
        else:
            action = ACTION_REQUEST_REVIEW
            requires_human = True

        return Decision(
            action=action,
            requires_human=requires_human,
            reason=f"Governance review status is {review_status}.",
            target_node=_normalize_text(event.context.get("target_node")),
            metadata={
                "provider": self.provider_name,
                "review_status": review_status,
                "event_type": event.event_type,
            },
        )


def _summarize_rule_context(rule_context: dict[str, Any]) -> dict[str, Any]:
    """Keep logs compact while retaining the useful rule inputs."""

    payload = rule_context.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    return {
        "order_id": payload.get("order_id"),
        "patient_id": payload.get("patient_id"),
        "test_code": payload.get("test_code"),
        "duplicate_candidate": payload.get("duplicate_candidate"),
        "repeated_indicator": payload.get("repeated_indicator"),
        "possible_duplicate": payload.get("possible_duplicate"),
    }


def _summarize_event_context(context: dict[str, Any]) -> dict[str, Any]:
    """Strip bulky objects from the logged event context."""

    summary = {}
    for key in ("node_name", "node_type", "workflow_name", "matched_action", "human_review_status"):
        value = context.get(key)
        if value is not None:
            summary[key] = value
    return summary


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def governance_consultations_to_dicts(
    consultations: list[GovernanceConsultation | dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize consultation records for export."""

    normalized: list[dict[str, Any]] = []
    for consultation in consultations:
        if isinstance(consultation, GovernanceConsultation):
            normalized.append(consultation.to_dict())
        elif isinstance(consultation, dict):
            normalized.append(
                {
                    "sim_time": consultation.get("sim_time"),
                    "event_type": consultation.get("event_type"),
                    "node_id": consultation.get("node_id"),
                    "order_id": consultation.get("order_id"),
                    "specimen_id": consultation.get("specimen_id"),
                    "provider": consultation.get("provider") or consultation.get("provider_name"),
                    "provider_name": consultation.get("provider_name") or consultation.get("provider"),
                    "decision_action": consultation.get("decision_action"),
                    "requires_human": consultation.get("requires_human"),
                    "reason": consultation.get("reason"),
                    "metadata": deepcopy(consultation.get("metadata", {})),
                }
            )
        else:
            raise TypeError("consultations must contain GovernanceConsultation objects or dictionaries.")
    return normalized


def export_governance_consultations_to_json(
    consultations: list[GovernanceConsultation | dict[str, Any]],
    path: str | Path,
) -> Path:
    """Export governance consultations as JSON."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(governance_consultations_to_dicts(consultations), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return output_path


def export_governance_consultations_to_csv(
    consultations: list[GovernanceConsultation | dict[str, Any]],
    path: str | Path,
) -> Path:
    """Export governance consultations as a CSV table."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = governance_consultations_to_dicts(consultations)
    fieldnames = [
        "sim_time",
        "event_type",
        "node_id",
        "order_id",
        "specimen_id",
        "provider",
        "decision_action",
        "requires_human",
        "reason",
        "metadata",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "sim_time": row.get("sim_time", ""),
                    "event_type": row.get("event_type", ""),
                    "node_id": row.get("node_id", ""),
                    "order_id": row.get("order_id", ""),
                    "specimen_id": row.get("specimen_id", ""),
                    "provider": row.get("provider", ""),
                    "decision_action": row.get("decision_action", ""),
                    "requires_human": row.get("requires_human", ""),
                    "reason": row.get("reason", ""),
                    "metadata": json.dumps(row.get("metadata", {}), sort_keys=True, default=str),
                }
            )
    return output_path


def export_simulation_result_to_json(result: object, path: str | Path) -> Path:
    """Export a simulation result object as JSON using its structured serializer."""

    if hasattr(result, "to_dict") and callable(getattr(result, "to_dict")):
        payload = result.to_dict()
    elif isinstance(result, dict):
        payload = result
    else:
        raise TypeError("result must provide a to_dict() method or already be a dictionary.")

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return output_path
