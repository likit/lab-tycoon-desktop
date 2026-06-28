"""Base classes for specimen-processing workflow widgets."""

from __future__ import annotations

from collections import deque
from typing import Deque
from typing import Any

from .specimen import Specimen


class BaseWidget:
    """Base class for a node in a specimen-processing workflow.

    Widgets are part of the model layer. They own local queue state and
    domain-specific processing rules, but they do not decide when processing
    starts or when downstream delivery occurs. That orchestration belongs to
    the simulation engine.
    """

    def __init__(
        self,
        name: str,
        service_time: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.service_time = service_time
        self.queue: Deque[Specimen] = deque()
        self.downstream_widgets: list[BaseWidget] = []
        self.processed_count = 0
        self.is_busy = False
        self.metadata: dict[str, Any] = dict(metadata or {})

    @property
    def node_type(self) -> str:
        """Return the workflow node type stored in metadata."""

        return str(self.metadata.get("node_type", "specimens processing"))

    @node_type.setter
    def node_type(self, value: str) -> None:
        self.metadata["node_type"] = value

    @property
    def throughput(self) -> float:
        """Return the configured throughput."""

        return float(self.metadata.get("throughput", 0.0))

    @throughput.setter
    def throughput(self, value: float) -> None:
        self.metadata["throughput"] = float(value)

    @property
    def human_throughput(self) -> float:
        """Return the configured human throughput."""

        return float(self.metadata.get("human_throughput", 0.0))

    @human_throughput.setter
    def human_throughput(self, value: float) -> None:
        self.metadata["human_throughput"] = float(value)

    @property
    def capacity(self) -> int:
        """Return the configured simulation capacity."""

        return max(1, int(self.metadata.get("capacity", 1)))

    @capacity.setter
    def capacity(self, value: int) -> None:
        self.metadata["capacity"] = max(1, int(value))

    @property
    def description(self) -> str:
        """Return the configured description."""

        return str(self.metadata.get("description", ""))

    @description.setter
    def description(self, value: str) -> None:
        self.metadata["description"] = value

    @property
    def required_staff(self) -> int:
        """Return the required staff count."""

        return int(self.metadata.get("required_staff", 0))

    @required_staff.setter
    def required_staff(self, value: int) -> None:
        self.metadata["required_staff"] = int(value)

    def connect(self, widget: "BaseWidget") -> "BaseWidget":
        """Connect this widget to a downstream widget.

        Returns the downstream widget to support fluent graph construction.
        """

        if widget is self:
            raise ValueError("A widget cannot connect to itself.")

        if widget not in self.downstream_widgets:
            self.downstream_widgets.append(widget)

        return widget

    def disconnect(self, widget: "BaseWidget") -> None:
        """Remove a downstream widget connection if it exists."""

        self.downstream_widgets = [candidate for candidate in self.downstream_widgets if candidate is not widget]

    def receive(self, specimen: Specimen) -> None:
        """Accept a specimen and place it in the internal queue."""

        specimen.record_event(self.name, "received", queue_size=len(self.queue) + 1)
        self.queue.append(specimen)

    def process(self) -> Specimen | None:
        """Process a single queued specimen and return it to the engine.

        The widget itself does not forward the specimen. The engine decides when
        processing starts, when it completes in simulation time, and where the
        specimen is sent next.
        """

        if not self.queue:
            return None

        specimen = self.queue.popleft()
        specimen.record_event(self.name, "processing_started", service_time=self.service_time)

        # Subclasses can override this hook to mutate, inspect, or validate the
        # specimen before the engine forwards it downstream.
        self.handle_specimen(specimen)

        self.processed_count += 1
        specimen.record_event(
            self.name,
            "processing_finished",
            processed_count=self.processed_count,
        )
        return specimen

    def send(self, specimen: Specimen) -> None:
        """Forward a specimen to each connected downstream widget.

        This method remains on the model object as a convenience helper, but it
        is expected to be called by the engine rather than by :meth:`process`.
        """

        if not self.downstream_widgets:
            specimen.record_event(self.name, "completed")
            return

        for widget in self.downstream_widgets:
            specimen.record_event(widget.name, "queued_from", source=self.name)
            widget.receive(specimen)

    def handle_specimen(self, specimen: Specimen) -> None:
        """Hook for subclasses to implement widget-specific behavior."""

        return None

    def to_dict(self) -> dict[str, object]:
        """Serialize the widget configuration for persistence."""

        return {
            "type": self.__class__.__name__,
            "name": self.name,
            "service_time": self.service_time,
            "metadata": dict(self.metadata),
            "node_type": self.node_type,
            "throughput": self.throughput,
            "human_throughput": self.human_throughput,
            "capacity": self.capacity,
            "description": self.description,
            "required_staff": self.required_staff,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "BaseWidget":
        """Deserialize a widget from stored workflow data."""

        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        metadata = dict(metadata)
        if "node_type" not in metadata and data.get("node_type") is not None:
            metadata["node_type"] = str(data.get("node_type"))
        if "throughput" not in metadata and data.get("throughput") is not None:
            metadata["throughput"] = float(data.get("throughput", 0.0))
        if "human_throughput" not in metadata and data.get("human_throughput") is not None:
            metadata["human_throughput"] = float(data.get("human_throughput", 0.0))
        if "capacity" not in metadata and data.get("capacity") is not None:
            metadata["capacity"] = int(data.get("capacity", 1))
        if "description" not in metadata and data.get("description") is not None:
            metadata["description"] = str(data.get("description", ""))
        if "required_staff" not in metadata and data.get("required_staff") is not None:
            metadata["required_staff"] = int(data.get("required_staff", 0))

        return cls(
            name=str(data.get("name", "Widget")),
            service_time=float(data.get("service_time", 0.0)),
            metadata=metadata,
        )
