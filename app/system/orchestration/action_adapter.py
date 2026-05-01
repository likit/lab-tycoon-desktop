"""Narrow adapter for workflow-originated LabTycoon actions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class WorkflowActionRecord:
    """Passive record created by workflow-side recommendations."""

    record_id: str
    entity_type: str
    entity_id: str
    source: str
    kind: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


class WorkflowActionAdapter:
    """Controlled write boundary from workflow logic into LabTycoon.

    This first adapter is intentionally in-memory and informational only. It
    does not mutate operational LIS entities, simulation state, routing, timing,
    or branching.
    """

    def __init__(self) -> None:
        self._records: list[WorkflowActionRecord] = []

    def create_review_flag(
        self,
        entity_type: str,
        entity_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a passive review flag linked to an entity."""

        entity_type = _validate_non_empty("entity_type", entity_type)
        entity_id = _validate_non_empty("entity_id", entity_id)
        reason = _validate_non_empty("reason", reason)

        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dictionary when provided.")

        record = WorkflowActionRecord(
            record_id=uuid4().hex,
            entity_type=entity_type,
            entity_id=entity_id,
            source="workflow",
            kind="review_flag",
            message=reason,
            metadata=deepcopy(metadata or {}),
            created_at=datetime.now(UTC).isoformat(),
        )
        self._records.append(record)
        return record.record_id

    def get_entity_flags(self, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
        """Return passive review flags associated with an entity."""

        entity_type = _validate_non_empty("entity_type", entity_type)
        entity_id = _validate_non_empty("entity_id", entity_id)

        return [
            asdict(record)
            for record in self._records
            if record.kind == "review_flag"
            and record.entity_type == entity_type
            and record.entity_id == entity_id
        ]


def _validate_non_empty(field_name: str, value: str) -> str:
    """Validate simple adapter inputs before creating passive records."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()
