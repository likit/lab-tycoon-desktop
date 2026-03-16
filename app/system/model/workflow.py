"""Workflow graph definitions for the LabTycoon domain model."""

from __future__ import annotations

from dataclasses import dataclass, field

from .specimen import Specimen
from .widget import BaseWidget


@dataclass(slots=True)
class Scenario:
    """A parameter set used to run the same workflow under different conditions.

    Scenarios let one workflow structure be reused with different assumptions,
    such as alternative service times, staffing levels, or specimen arrival
    profiles.
    """

    name: str
    description: str = ""
    widget_service_times: dict[str, float] = field(default_factory=dict)
    parameters: dict[str, float | int | str | bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Serialize the scenario for storage."""

        return {
            "name": self.name,
            "description": self.description,
            "widget_service_times": dict(self.widget_service_times),
            "parameters": dict(self.parameters),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Scenario":
        """Deserialize a scenario from stored data."""

        return cls(
            name=str(data.get("name", "Default Scenario")),
            description=str(data.get("description", "")),
            widget_service_times=dict(data.get("widget_service_times", {})),
            parameters=dict(data.get("parameters", {})),
        )


class Workflow:
    """Store the widget network that defines a simulation model.

    The workflow belongs to the model layer. It tracks structure and entry
    points, but it does not advance time or execute scheduled events.
    """

    def __init__(self) -> None:
        self.widgets: list[BaseWidget] = []
        self.scenarios: list[Scenario] = [Scenario(name="Default Scenario")]

    def add_widget(self, widget: BaseWidget) -> BaseWidget:
        """Register a widget with the workflow if it is not already present."""

        if widget not in self.widgets:
            self.widgets.append(widget)
        return widget

    def connect(self, a: BaseWidget, b: BaseWidget) -> None:
        """Connect widget ``a`` to widget ``b`` and register both widgets."""

        self.add_widget(a)
        self.add_widget(b)
        a.connect(b)

    def send_to(self, widget: BaseWidget, specimen: Specimen) -> None:
        """Inject a specimen into a specific widget in the workflow."""

        self.add_widget(widget)
        specimen.record_event(widget.name, "injected")
        widget.receive(specimen)

    def add_scenario(self, scenario: Scenario) -> Scenario:
        """Register a scenario for this workflow."""

        if all(existing.name != scenario.name for existing in self.scenarios):
            self.scenarios.append(scenario)
        return scenario

    def to_dict(self) -> dict[str, object]:
        """Serialize the workflow graph and its scenario definitions."""

        widget_index = {widget: index for index, widget in enumerate(self.widgets)}
        connections = []
        for source_widget in self.widgets:
            for target_widget in source_widget.downstream_widgets:
                connections.append(
                    {
                        "source": widget_index[source_widget],
                        "target": widget_index[target_widget],
                    }
                )

        return {
            "widgets": [widget.to_dict() for widget in self.widgets],
            "connections": connections,
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Workflow":
        """Deserialize a workflow graph from stored data."""

        workflow = cls()
        workflow.widgets = []

        widgets_by_index: list[BaseWidget] = []
        for widget_data in data.get("widgets", []):
            if not isinstance(widget_data, dict):
                continue
            widget = BaseWidget(
                name=str(widget_data.get("name", "Widget")),
                service_time=float(widget_data.get("service_time", 0.0)),
            )
            workflow.add_widget(widget)
            widgets_by_index.append(widget)

        for connection in data.get("connections", []):
            if not isinstance(connection, dict):
                continue
            source_index = int(connection.get("source", -1))
            target_index = int(connection.get("target", -1))
            if 0 <= source_index < len(widgets_by_index) and 0 <= target_index < len(widgets_by_index):
                workflow.connect(widgets_by_index[source_index], widgets_by_index[target_index])

        scenarios = []
        for scenario_data in data.get("scenarios", []):
            if isinstance(scenario_data, dict):
                scenarios.append(Scenario.from_dict(scenario_data))
        workflow.scenarios = scenarios or [Scenario(name="Default Scenario")]
        return workflow
