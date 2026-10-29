"""Paced SimPy session. Worker owns the engine; the GUI consumes snapshots."""
from copy import deepcopy
from collections import deque
from queue import SimpleQueue
from threading import Condition, Thread
from time import monotonic
from math import isfinite
import random

from app.system.staff_activity import staff_activity
from app.system.equipment_workflow import resolve_equipment_workflow
from app.system.model import Workflow, Scenario
from app.system.simulation import WorkflowSimulationEngine, WorkflowSimulationSettings
from app.system.staff_batching import staff_batch_settings


DEFAULT_SIMULATION_SPEED = .1


def simulation_progress(engine):
    """Count orders and requested tests consistently in every floor window."""
    completed = {item.specimen_id for item in engine.completed_specimens}
    orders = {}
    tests_total = tests_completed = 0
    for specimen in engine.specimens:
        order_id = str(specimen.metadata.get('order_id', specimen.specimen_id))
        orders.setdefault(order_id, set()).add(specimen.specimen_id)
        tests = specimen.metadata.get('all_tests', [])
        tests_total += len(tests) or 1
        tests_completed += len(specimen.metadata.get('completed_tests', [])) if tests else int(specimen.specimen_id in completed)
    return dict(orders_rejected=len(engine.rejected_specimens), orders_total=len(orders), orders_completed=sum(ids <= completed for ids in orders.values()),
                tests_total=tests_total, tests_completed=tests_completed)


class LiveSimulationSession:
    def __init__(self, workflow, *, scenario=None, count=10, seed=None, speed=DEFAULT_SIMULATION_SPEED, staff_capacity=None, equipment_types=None, on_complete=None,
                 simulation_engine=None, display_workflow=None):
        if count < 1 or not isfinite(speed) or speed <= 0:
            raise ValueError('Order count and speed must be positive.')
        if staff_capacity is not None and (not isinstance(staff_capacity, int) or staff_capacity < 1):
            raise ValueError('Available staff must be a positive whole number.')
        self.workflow = Workflow.from_dict(workflow.to_dict())
        self.scenario = Scenario.from_dict(scenario.to_dict()) if scenario else self.workflow.active_scenario
        self.events = SimpleQueue()
        self.condition = Condition()
        self.state = 'stopped'
        self.elapsed = 0.0
        self.base_speed = speed
        self.speed = speed  # simulated hours per wall-clock second
        self.error = None
        self.generated_orders = []
        self.result = None
        self._on_complete = on_complete
        self._thread = None
        self._counts = {}
        self._node_work = {}
        self._order_routes = {}
        self.staff_assignments = {}
        self.staff_waiting = {}
        self._staff_random = random.Random(seed)
        self.equipment_types = dict(equipment_types or {})
        self.workflow = resolve_equipment_workflow(self.workflow, self.equipment_types)
        engine_workflow = self.workflow
        if display_workflow is not None:
            # Display-only placements must never change resource ownership,
            # node kinds or capacities in the actual simulation workflow.
            self.workflow = resolve_equipment_workflow(
                Workflow.from_dict(display_workflow.to_dict()), self.equipment_types)
        self._last_equipment = {}
        self.transfers = []
        self._in_flight = {}
        self._recent_transfers = []
        self.results_feed = deque(maxlen=200)
        self._result_keys = set()
        self._result_sequence = 0
        settings = WorkflowSimulationSettings.from_scenario(
            specimens_number=count, workflow=engine_workflow, scenario=self.scenario, random_seed=seed)
        if staff_capacity is not None:
            settings.staff_capacity = staff_capacity
            self.scenario.parameters['staff_capacity'] = staff_capacity
        self._bind_engine(simulation_engine or WorkflowSimulationEngine(
            engine_workflow, settings=settings, scenario=self.scenario,
            on_activity=self._activity, on_order_created=self.generated_orders.append))

    def _bind_engine(self, engine):
        """Map engine resources to the displayed floor without changing execution."""
        self.engine = engine
        self._display_equipment_resources = {}
        for node in self.workflow.graph.iter_nodes():
            equipment_id = node.metadata.get('equipment_id')
            resource = self.engine.node_resources.get(node.node_id)
            if (node.kind != 'equipment' or not equipment_id or resource is None
                    or equipment_id in self.engine.equipment_resources):
                continue
            resources = self._display_equipment_resources.setdefault(equipment_id, [])
            if resource not in resources:
                resources.append(resource)
        if not self.engine.active_node_ids:
            raise ValueError('Connect an enabled workflow source before starting the simulation.')

    def set_speed(self, speed):
        if not isfinite(speed) or speed <= 0:
            raise ValueError('Simulation speed must be positive.')
        with self.condition:
            self.speed = speed
            self.condition.notify_all()

    def wait(self):
        """Join the worker before an analysis modal returns to the LIS."""
        if self._thread is not None:
            self._thread.join()

    def _activity(self, event):
        if event['event'] == 'staff_waiting':
            self.staff_waiting[(event['node_id'], event['specimen_id'])] = dict(event)
            self.events.put(dict(event))
            return
        if event['event'] in ('staff_acquired', 'staff_released'):
            key = (event['node_id'], event['specimen_id'])
            self.staff_waiting.pop(key, None)
            if event['event'] == 'staff_released':
                self.staff_assignments.pop(key, None)
            else:
                node = self.workflow.get_node(event['node_id'])
                equipment_id = node.metadata.get('attend_equipment_id') or node.metadata.get('equipment_id')
                activity = staff_activity(node, self.equipment_types.get(equipment_id))
                if not equipment_id:
                    kind = {'registration': 'specimens_reception_station',
                            'reporting': 'reporting_station', 'validating': 'reporting_station',
                            'processing': 'centrifuge'}[activity]
                    choices = [key for key, value in self.equipment_types.items() if value == kind]
                    equipment_id = choices[0] if len(choices) == 1 else None
                self.staff_assignments[key] = dict(event, equipment_id=equipment_id, activity=activity,
                    gender=self._staff_random.choice(('female', 'male')))
            self.events.put(dict(event))
            return
        counts = self._counts.setdefault(event['node_id'], {'queued': 0, 'active': 0, 'finished': 0})
        if event['event'] == 'arrived':
            counts['queued'] += 1
            counts['last_arrived'] = event['time']
        elif event['event'] == 'started':
            counts['queued'] = max(0, counts['queued'] - 1)
            counts['active'] += 1
        else:
            key = 'active' if counts['active'] else 'queued'
            counts[key] = max(0, counts[key] - 1)
            counts['finished'] += 1
            counts['last_finished'] = event['time']
        node = self.workflow.get_node(event['node_id'])
        specimen_id = event['specimen_id']
        key = (event['node_id'], specimen_id)
        if event['event'] == 'arrived':
            self._node_work[key] = dict(node_id=event['node_id'], specimen_id=specimen_id,
                order_id=str(event.get('order_id', specimen_id)), state='Queued', since=event['time'])
            route = self._order_routes.setdefault(str(event.get('order_id', specimen_id)), [])
            if not route or route[-1] != event['node_id']:
                route.append(event['node_id'])
        elif event['event'] == 'started':
            item = self._node_work.setdefault(key, dict(node_id=event['node_id'],
                specimen_id=specimen_id, order_id=str(specimen_id)))
            item.update(state='Processing', since=event['time'])
        else:
            self._node_work.pop(key, None)
        if event['event'] == 'arrived':
            self._record_reporting_result(event, node)
            if node.kind == 'equipment':
                self._in_flight.pop(specimen_id, None)
                equipment_id = str(node.metadata.get('equipment_id', ''))
                sources = event.get('source_equipment_ids')
                if not sources:
                    sources = [self._last_equipment.get(specimen_id)]
                for source in sources:
                    if source and source != equipment_id:
                        self._recent_transfers.append({'source': source, 'target': equipment_id,
                            'time': event['time'], 'specimen_id': specimen_id, 'delivered': True})
                self._last_equipment[specimen_id] = equipment_id
            elif specimen_id in self._last_equipment:
                # Follow the route actually reached by this specimen. Only
                # display a projected leg once it has an unambiguous endpoint.
                pending, seen, targets = [node.node_id], set(), set()
                while pending:
                    node_id = pending.pop()
                    if node_id in seen:
                        continue
                    seen.add(node_id)
                    candidate = self.workflow.get_node(node_id)
                    if candidate.kind == 'equipment':
                        targets.add(str(candidate.metadata.get('equipment_id', '')))
                    else:
                        pending.extend(edge.target for edge in self.workflow.graph.outgoing_edges(node_id))
                if len(targets) == 1:
                    target = next(iter(targets))
                    previous = self._in_flight.get(specimen_id)
                    if previous is None or previous['target'] != target:
                        self._in_flight[specimen_id] = {'source': self._last_equipment[specimen_id],
                            'target': target, 'time': event['time'], 'specimen_id': specimen_id}
        self._recent_transfers = [transfer for transfer in self._recent_transfers
                                  if event['time'] - transfer['time'] < .04]
        self.transfers = list(self._in_flight.values()) + self._recent_transfers
        self.events.put(dict(event))

    def _record_reporting_result(self, event, node):
        """Record data arrival even when legacy equipment supplies no test details."""
        equipment_id = str(node.metadata.get('equipment_id') or
                           node.metadata.get('attend_equipment_id') or '')
        reporting_process = node.widget.node_type in ('result reporting', 'result validating')
        if not equipment_id and reporting_process:
            stations = [key for key, kind in self.equipment_types.items() if kind == 'reporting_station']
            equipment_id = stations[0] if len(stations) == 1 else ''
        reporting = (self.equipment_types.get(equipment_id) == 'reporting_station'
                     or node.metadata.get('terminal_output'))
        if not reporting or not equipment_id:
            return
        sources = event.get('source_equipment_ids')
        if not sources:
            sources = [self._last_equipment.get(event['specimen_id'])]
        analyzer_ids = {str(candidate.metadata.get('equipment_id'))
                        for candidate in self.workflow.graph.iter_nodes()
                        if candidate.widget.node_type in ('automated analyzer', 'manual analyzer')}
        sources = list(dict.fromkeys(source for source in sources
                       if self.equipment_types.get(source) == 'chemistry_analyzer'
                       or source in analyzer_ids))
        tests = tuple(event.get('completed_tests', []))
        key = (str(event['specimen_id']), equipment_id, tests)
        if not sources or key in self._result_keys:
            return
        self._result_keys.add(key)
        self._result_sequence += 1
        self.results_feed.append({'sequence': self._result_sequence,
            'time': event['time'], 'order_id': str(event.get('order_id', event['specimen_id'])),
            'tests': list(tests), 'sources': sources, 'target': equipment_id})

    def play(self):
        with self.condition:
            if self.state in ('completed', 'failed', 'cancelled'):
                return
            self.state = 'playing'
            if self._thread is None:
                self._thread = Thread(target=self._run, daemon=True)
                self._thread.start()
            self.condition.notify_all()

    def pause(self):
        with self.condition:
            if self.state == 'playing':
                self.state = 'paused'
                self.condition.notify_all()

    def stop(self):
        with self.condition:
            if self.state not in ('completed', 'failed', 'saving'):
                self.state = 'cancelled'
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            equipment_capacity = {
                equipment_id: {'used': resource.count, 'total': resource.capacity}
                for equipment_id, resource in self.engine.equipment_resources.items()}
            equipment_capacity.update({
                equipment_id: {'used': sum(resource.count for resource in resources),
                               'total': sum(resource.capacity for resource in resources)}
                for equipment_id, resources in self._display_equipment_resources.items()})
            specimens = {item.specimen_id: item for item in self.engine.specimens}
            name_counts = {}
            for node in self.engine.workflow.graph.iter_nodes():
                name_counts[node.widget.name] = name_counts.get(node.widget.name, 0) + 1
            work = []
            for item in self._node_work.values():
                view = dict(item)
                specimen = specimens.get(item['specimen_id'])
                view.update(tests=list(specimen.metadata.get('all_tests', [])) if specimen else [],
                            completed_tests=list(dict.fromkeys(
                                list(specimen.metadata.get('completed_tests', [])) +
                                [entry['details']['test_code'] for entry in specimen.history
                                 if entry['event'] == 'test_finished' and 'test_code' in entry.get('details', {})])) if specimen else [])
                node = self.engine.workflow.get_node(item['node_id'])
                if (specimen is not None and item['state'] == 'Processing' and node is not None
                        and name_counts[node.widget.name] == 1):
                    history = [entry for entry in specimen.history if entry.get('widget') == node.widget.name
                               and entry.get('details', {}).get('time', -1) >= item['since']]
                    if history and node.widget.node_type == 'automated analyzer' and history[-1]['event'] == 'human_started':
                        view['state'] = 'Unloading' if any(entry['event'] == 'test_finished' for entry in history) else 'Loading'
                interval = self.engine.active_processing.get((item['node_id'], item['specimen_id']))
                if interval and interval['end'] > interval['start']:
                    duration = interval['end'] - interval['start']
                    view['progress'] = dict(phase=interval['phase'],
                        fraction=max(0, min(1, (self.elapsed - interval['start']) / duration)),
                        remaining_seconds=max(0, (interval['end'] - self.elapsed) * 3600))
                work.append(view)
            batches = {}
            for node_id, queue in self.engine._batch_queues.items():
                size, wait = staff_batch_settings(self.engine.workflow.get_node(node_id))
                batches[node_id] = dict(collected=len(queue), size=size,
                    remaining_seconds=max(0, (queue[0][2] + wait / 60 - self.elapsed) * 3600) if queue else 0)
            return {**simulation_progress(self.engine), 'state': self.state, 'time': self.elapsed, 'counts': deepcopy(self._counts),
                    'work_items': work, 'batches': batches, 'order_routes': deepcopy(self._order_routes),
                    'completed': len(self.engine.completed_specimens), 'total': len(self.engine.specimens),
                    'error': self.error, 'transfers': deepcopy(self.transfers),
                    'results_feed': deepcopy(list(self.results_feed)),
                    'equipment_capacity': equipment_capacity,
                    'staff_capacity': self.engine.staff_capacity,
                    'staff_available': self.engine.staff_pool.level,
                    'staff_waiting': deepcopy(list(self.staff_waiting.values())),
                    'equipment_waiting': {node_id: len(resource.queue)
                                          for node_id, resource in self.engine.node_resources.items()},
                    'staff_assignments': deepcopy(list(self.staff_assignments.values()))}

    def _execute_engine(self):
        """One paced SimPy loop shared by generated orders and selected LIS work."""
        arrivals = self.engine.env.process(self.engine._arrival_process())
        last = monotonic()
        while True:
            with self.condition:
                if self.state == 'cancelled':
                    return None
                if self.state == 'paused':
                    self.condition.wait()
                    last = monotonic()
                    continue
                now = monotonic()
                target = self.elapsed + (now - last) * self.speed
                last = now
                # Bound each batch so zero-time chains cannot starve Pause/Stop.
                steps = 0
                while self.engine.env.peek() <= target and steps < 1000 and not arrivals.processed:
                    self.engine.env.step()
                    steps += 1
                self.elapsed = min(target, self.engine.env.peek())
                if arrivals.processed or self.engine.env.peek() == float('inf'):
                    self.elapsed = float(self.engine.env.now)
                    self.state = 'saving'
                    return self.engine.result_summary()
                self.condition.wait(timeout=.02)

    def _run(self):
        try:
            if self._execute_engine() is None:
                return
            if self._on_complete:
                self.result = self._on_complete(self)
            with self.condition:
                self.state = 'completed'
        except Exception as exc:
            with self.condition:
                self.error = str(exc)
                self.state = 'failed'
