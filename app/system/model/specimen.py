"""Specimen objects that travel through a workflow graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Specimen:
    """A specimen moving through the simulation model.

    The model layer keeps specimens lightweight so they can be manipulated by
    the engine without any dependency on UI state or rendering concerns.
    """

    specimen_id: str
    payload: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    def record_event(self, widget_name: str, event: str, **details: Any) -> None:
        """Append a model or engine event to the specimen history."""

        entry = {"widget": widget_name, "event": event}
        if details:
            entry["details"] = details
        self.history.append(entry)
