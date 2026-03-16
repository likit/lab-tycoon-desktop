"""Base classes for specimen-processing workflow widgets."""

from __future__ import annotations

from collections import deque
from typing import Deque

from .specimen import Specimen


class BaseWidget:
    """Base class for a node in a specimen-processing workflow.

    Widgets are part of the model layer. They own local queue state and
    domain-specific processing rules, but they do not decide when processing
    starts or when downstream delivery occurs. That orchestration belongs to
    the simulation engine.
    """

    def __init__(self, name: str, service_time: float = 0.0) -> None:
        self.name = name
        self.service_time = service_time
        self.queue: Deque[Specimen] = deque()
        self.downstream_widgets: list[BaseWidget] = []
        self.processed_count = 0
        self.is_busy = False

    def connect(self, widget: "BaseWidget") -> "BaseWidget":
        """Connect this widget to a downstream widget.

        Returns the downstream widget to support fluent graph construction.
        """

        if widget is self:
            raise ValueError("A widget cannot connect to itself.")

        if widget not in self.downstream_widgets:
            self.downstream_widgets.append(widget)

        return widget

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
            "metadata": {},
        }
