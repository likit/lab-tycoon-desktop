"""Types and event names for LabTycoon domain events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class DomainEventName:
    """Initial domain event names for future orchestration use."""

    ORDER_CREATED = "order_created"
    SAMPLE_RECEIVED = "sample_received"
    RESULT_GENERATED = "result_generated"
    QC_FAILED = "qc_failed"
    CRITICAL_VALUE_DETECTED = "critical_value_detected"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """A small immutable event object published by core LabTycoon code."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[DomainEvent], None]
