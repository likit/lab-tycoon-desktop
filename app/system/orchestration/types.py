"""Small public types for workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PendingTask:
    """A task waiting for application/user action."""

    task_id: str
    workflow_id: str
    name: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowObservation:
    """Read-only output produced by passive workflow proof-of-concept logic."""

    observation_id: str
    observed_event: str
    process_name: str
    entity_type: str | None
    entity_id: str | None
    recommendation: str
    context_summary: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""


WorkflowStatus = dict[str, Any]
