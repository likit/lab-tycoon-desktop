"""Discrete-event simulation clock utilities.

The simulation clock advances in jumps rather than real time. Each call to
``step()`` moves ``current_time`` directly to the next scheduled event and then
executes its callback. This makes the clock suitable for workflow simulations
where processing stations finish work at specific simulated times.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
from itertools import count
from typing import Any, Callable


@dataclass(order=True, slots=True)
class ScheduledEvent:
    """A single event waiting to be executed by the simulation clock."""

    scheduled_time: float
    sequence: int
    callback: Callable[..., Any] = field(compare=False)
    args: tuple[Any, ...] = field(default_factory=tuple, compare=False)


class SimulationClock:
    """Manage time progression for a discrete-event simulation.

    Time only advances when an event is executed or when ``run_until()`` is
    asked to move the clock to a future limit. The clock does not sleep or use
    wall-clock time, which keeps it independent from UI, rendering, and frame
    rate concerns.
    """

    def __init__(self) -> None:
        self.current_time = 0.0
        self.event_queue: list[ScheduledEvent] = []
        self._sequence = count()

    def schedule(self, delay: float, callback: Callable[..., Any], *args: Any) -> ScheduledEvent:
        """Schedule ``callback`` to run after ``delay`` units of simulation time."""

        if delay < 0:
            raise ValueError("Event delay cannot be negative.")

        return self.schedule_at(self.current_time + delay, callback, *args)

    def schedule_at(
        self, time: float, callback: Callable[..., Any], *args: Any
    ) -> ScheduledEvent:
        """Schedule ``callback`` to run at an absolute simulation time."""

        if time < self.current_time:
            raise ValueError("Cannot schedule an event in the past.")

        event = ScheduledEvent(
            scheduled_time=time,
            sequence=next(self._sequence),
            callback=callback,
            args=args,
        )
        heapq.heappush(self.event_queue, event)
        return event

    def step(self) -> ScheduledEvent | None:
        """Execute the next event and advance ``current_time`` to its timestamp."""

        if not self.event_queue:
            return None

        event = heapq.heappop(self.event_queue)
        self.current_time = event.scheduled_time
        event.callback(*event.args)
        return event

    def run_until(self, time_limit: float) -> None:
        """Run all events scheduled up to ``time_limit``.

        Events are executed in chronological order. After all due events have
        run, the clock advances to ``time_limit`` even if no event exists
        exactly at that moment.
        """

        if time_limit < self.current_time:
            raise ValueError("Time limit cannot be earlier than the current time.")

        while self.event_queue and self.event_queue[0].scheduled_time <= time_limit:
            self.step()

        self.current_time = time_limit

    def run(self) -> None:
        """Run events until the queue is empty."""

        while self.event_queue:
            self.step()


if __name__ == "__main__":
    clock = SimulationClock()

    def announce(label: str) -> None:
        print(f"{clock.current_time:.1f}: {label}")

    clock.schedule(2.0, announce, "specimen finished incubation")
    clock.schedule(0.5, announce, "specimen entered centrifuge")
    clock.schedule_at(3.0, announce, "specimen reached output node")
    clock.run()
