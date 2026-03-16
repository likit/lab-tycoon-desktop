"""Simulation orchestration for workflow-based laboratory models."""

from __future__ import annotations

from ..model.specimen import Specimen
from ..model.widget import BaseWidget
from ..model.workflow import Scenario, Workflow
from .simulation_clock import ScheduledEvent, SimulationClock


class Simulator:
    """Run a workflow model on top of a discrete-event simulation clock.

    The simulator owns execution state and scheduling. Widgets remain pure model
    objects, while the simulator decides when a queued specimen starts
    processing and when downstream delivery occurs.
    """

    def __init__(
        self,
        workflow: Workflow,
        clock: SimulationClock | None = None,
        scenario: Scenario | None = None,
    ) -> None:
        self.workflow = workflow
        self.clock = clock or SimulationClock()
        self.scenario = scenario
        self.completed_specimens: list[Specimen] = []
        self._base_service_times = {
            widget.name: widget.service_time for widget in self.workflow.widgets
        }
        if scenario is not None:
            self.apply_scenario(scenario)

    def add_widget(self, widget: BaseWidget) -> BaseWidget:
        """Register a widget in the underlying workflow model."""

        return self.workflow.add_widget(widget)

    def connect(self, a: BaseWidget, b: BaseWidget) -> None:
        """Connect two widgets in the underlying workflow model."""

        self.workflow.connect(a, b)

    def send_to(self, widget: BaseWidget, specimen: Specimen) -> None:
        """Inject a specimen and attempt to start work on the target widget."""

        self.workflow.send_to(widget, specimen)
        self._try_start_widget(widget)

    def step(self) -> ScheduledEvent | None:
        """Execute the next scheduled engine event."""

        return self.clock.step()

    def run_until(self, time_limit: float) -> None:
        """Advance the simulation until the requested simulation time."""

        self.clock.run_until(time_limit)

    def run(self) -> None:
        """Run the simulation until no scheduled events remain."""

        self.clock.run()

    def apply_scenario(self, scenario: Scenario) -> None:
        """Apply scenario overrides to widget parameters before a run."""

        self.scenario = scenario
        for widget in self.workflow.widgets:
            widget.service_time = self._base_service_times.get(widget.name, widget.service_time)

        for widget in self.workflow.widgets:
            if widget.name in scenario.widget_service_times:
                widget.service_time = scenario.widget_service_times[widget.name]

    def _try_start_widget(self, widget: BaseWidget) -> None:
        """Schedule processing if the widget is idle and has queued work."""

        if widget.is_busy or not widget.queue:
            return

        widget.is_busy = True
        queued_specimen = widget.queue[0]
        queued_specimen.record_event(
            widget.name,
            "processing_scheduled",
            scheduled_time=self.clock.current_time + widget.service_time,
        )
        self.clock.schedule(widget.service_time, self._complete_processing, widget)

    def _complete_processing(self, widget: BaseWidget) -> None:
        """Finish work at a widget and propagate the specimen onward."""

        specimen = widget.process()
        widget.is_busy = False

        if specimen is None:
            return

        specimen.record_event(
            widget.name,
            "released",
            time=self.clock.current_time,
        )

        if widget.downstream_widgets:
            for downstream_widget in widget.downstream_widgets:
                specimen.record_event(
                    downstream_widget.name,
                    "queued_from",
                    source=widget.name,
                    time=self.clock.current_time,
                )
                downstream_widget.receive(specimen)
                self._try_start_widget(downstream_widget)
        else:
            specimen.record_event(widget.name, "completed", time=self.clock.current_time)
            self.completed_specimens.append(specimen)

        self._try_start_widget(widget)
