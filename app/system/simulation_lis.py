"""Explicitly copy saved simulation orders into the operational LIS for teaching."""
from dataclasses import dataclass
from datetime import date, datetime
import json
from random import Random
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.system.models import (Customer, LabOrder, LabOrderItem, SimulationOrder,
                               SimulationRun, SimulationLISImport, SimulationLISBatchOrder, LISDemonstrationOrder, Test, engine)
from app.system.order_sources import SQLiteOrderSource
from app.auth.windows import login_required


def requested_codes(order):
    try:
        codes = json.loads(order.test_codes_json)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Simulation order {order.order_id}: invalid test list.') from exc
    if not isinstance(codes, list) or not codes or any(not isinstance(c, str) or not c.strip() for c in codes):
        raise ValueError(f'Simulation order {order.order_id}: a non-empty test list is required.')
    return list(dict.fromkeys(codes))


@dataclass(frozen=True)
class ImportSummary:
    created: int
    skipped: int
    run_id: str
    batch_id: str


def import_simulation_orders(run_id, *, test_mapping=None, database_engine=None):
    """Atomically create a fresh pending batch without modifying previous copies."""
    mapping = test_mapping or {}
    batch_id = uuid4().hex
    imported_at = datetime.now()
    with Session(database_engine or engine) as session, session.begin():
        if session.get(SimulationRun, run_id) is None:
            raise ValueError('The simulation run no longer exists.')
        orders = list(session.scalars(select(SimulationOrder).where(SimulationOrder.run_id == run_id)
                                      .options(selectinload(SimulationOrder.patient)).order_by(SimulationOrder.id)))
        if not orders:
            raise ValueError('This simulation run has no saved orders.')
        tests = {test.code: test for test in session.scalars(select(Test).where(Test.active.is_(True)))}
        prepared = []
        for order in orders:
            if order.priority not in ('routine', 'urgent'):
                raise ValueError(f'Simulation order {order.order_id}: invalid priority.')
            codes = list(dict.fromkeys(mapping.get(code, code) for code in requested_codes(order)))
            missing = [code for code in codes if code not in tests]
            if missing:
                raise ValueError('Map these codes to active LIS tests first: ' + ', '.join(missing))
            prepared.append((order, codes))
        patients = {}
        for source, codes in prepared:
            patient_key = (source.run_id, source.patient_id)
            hn = f'DEMO-{source.run_id}-{source.patient_id}'
            customer = patients.get(patient_key)
            if customer is None:
                customer = session.scalar(select(Customer).where(Customer.hn == hn))
                if customer is None:
                    name = source.patient.fullname if source.patient else f'Simulated Patient {source.patient_id}'
                    customer = Customer(hn=hn, firstname=f'[DEMO] {name}', lastname='', gender='Unknown',
                                        dob=date(2000, 1, 1), address='Synthetic demonstration patient; DOB is a placeholder (2000-01-01).')
                    session.add(customer)
                patients[patient_key] = customer
            order = LabOrder(customer=customer, order_datetime=source.ordered_at, priority=source.priority,
                             comment=f'DEMONSTRATION: batch {batch_id}, simulation run {source.run_id}, order {source.order_id}.')
            order.order_items = [LabOrderItem(test=tests[code]) for code in codes]
            session.add(SimulationLISBatchOrder(batch_id=batch_id, simulation_order_id=source.id, order=order,
                                                 imported_at=imported_at))
        return ImportSummary(len(prepared), 0, run_id, batch_id)


class SimulationLISOrderSource(SQLiteOrderSource):
    """Normal editable LIS orders, filtered to one imported simulation run."""
    label = 'Simulation'

    def __init__(self, run_id, *, batch_id=None, database_engine=None):
        self.run_id = run_id
        self.batch_id = batch_id
        self.database_engine = database_engine or engine

    def list_orders(self):
        from app.system.models import LabOrderItem
        with Session(self.database_engine) as session:
            batch_ids = (select(SimulationLISBatchOrder.lis_order_id)
                         .join(SimulationOrder, SimulationOrder.id == SimulationLISBatchOrder.simulation_order_id)
                         .where(SimulationOrder.run_id == self.run_id))
            if self.batch_id is not None:
                selected_ids = batch_ids.where(SimulationLISBatchOrder.batch_id == self.batch_id)
            else:
                legacy_ids = (select(SimulationLISImport.lis_order_id).join(SimulationOrder)
                              .where(SimulationOrder.run_id == self.run_id))
                selected_ids = batch_ids.union(legacy_ids)
            query = (select(LabOrder).where(LabOrder.id.in_(selected_ids))
                     .options(selectinload(LabOrder.customer), selectinload(LabOrder.doctor),
                              selectinload(LabOrder.order_items).selectinload(LabOrderItem.test))
                     .order_by(LabOrder.order_datetime, LabOrder.id))
            return list(session.scalars(query))

    def get_order(self, order_id):
        return next((order for order in self.list_orders() if str(order.id) == str(order_id)), None)


class ScenarioLISOrderSource(SimulationLISOrderSource):
    """All LIS demonstration orders generated from the selected workflow/scenario."""

    def __init__(self, choice, scenario_id, *, batch_id=None, database_engine=None):
        super().__init__('', batch_id=batch_id, database_engine=database_engine)
        self.choice = choice
        self.scenario_id = scenario_id
        self.scenario = next(s for s in choice.document.workflow.scenarios if s.scenario_id == scenario_id)

    def belongs_to_other_project(self):
        from app.project import project_session
        project = project_session.active_project
        return (project is not None and self.choice.project_id is not None
                and self.choice.project_id != project.project_id)

    def list_orders(self):
        with Session(self.database_engine) as session:
            query = (select(LabOrder).join(LISDemonstrationOrder)
                     .where(LISDemonstrationOrder.scenario_id == self.scenario_id,
                            LISDemonstrationOrder.workflow_id == self.choice.document.workflow_id)
                     .options(selectinload(LabOrder.customer), selectinload(LabOrder.doctor),
                              selectinload(LabOrder.order_items).selectinload(LabOrderItem.test))
                     .order_by(LabOrder.id))
            return list(session.scalars(query))


def generate_scenario_lis_orders(source, *, count):
    """Generate pending real-test orders; do not run the management simulation."""
    from datetime import timedelta
    from app.system.simulation.lab import WorkflowRouter
    from app.system.equipment_workflow import resolve_equipment_workflow
    if isinstance(count, bool) or int(count) != float(count) or int(count) < 1:
        raise ValueError('Number of orders must be a positive whole number.')
    count = int(count)
    if source.belongs_to_other_project():
        raise ValueError('The selected simulation scenario belongs to another project. '
                         'Choose the Simulation source again and select the current project scenario.')
    # The source picker stores a workflow snapshot. Prefer current settings
    # after scenario edits, retaining the snapshot only if its workflow is absent.
    from app.system.lis_analysis import analysis_workflow_choices
    latest = next((choice for choice in analysis_workflow_choices()
                   if choice.key == source.choice.key), None)
    if latest is not None:
        if not any(s.scenario_id == source.scenario_id for s in latest.document.workflow.scenarios):
            raise ValueError('The selected scenario was deleted. Select a scenario again.')
        source = ScenarioLISOrderSource(latest, source.scenario_id,
                                       database_engine=source.database_engine)
    workflow = resolve_equipment_workflow(source.choice.document.workflow)
    router = WorkflowRouter()
    reachable = set(router.reachable_node_ids(workflow, router.start_node_id(workflow)))
    analyzers = [node for node in workflow.graph.iter_nodes() if node.node_id in reachable
                 and node.widget.enabled and node.widget.node_type in ('automated analyzer', 'manual analyzer')]
    if not analyzers:
        raise ValueError('The scenario workflow needs an enabled analyzer.')
    selected = sorted({str(code) for node in analyzers for code in node.widget.metadata.get('analyzer_tests', [])})
    from app.system.arrivals import arrival_offsets
    parameters = source.scenario.parameters
    offsets = arrival_offsets(count, parameters.get('interarrival_time', 0),
        parameters.get('arrival_distribution', 'fixed'), parameters.get('arrival_seed'))
    from app.system.order_priorities import generated_priorities
    priorities = generated_priorities(count, parameters.get('stat_probability', 0),
                                      parameters.get('arrival_seed'))
    batch_id, generated_at = uuid4().hex, datetime.now()
    with Session(source.database_engine) as session, session.begin():
        catalogue = {test.code: test for test in session.scalars(select(Test).where(Test.active.is_(True)))}
        unavailable = set(selected) - set(catalogue)
        if unavailable:
            raise ValueError('Update the analyzer selection; these tests are inactive or missing: ' + ', '.join(sorted(unavailable)))
        codes = selected or sorted(catalogue)
        if not codes:
            raise ValueError('Add at least one active LIS test before loading orders.')
        test_seed = parameters.get('test_selection_seed', parameters.get('arrival_seed'))
        test_rng = Random(f'tests:{test_seed}' if test_seed is not None else None)
        min_tests = min(int(parameters.get('minimum_tests_per_order', 1)), len(codes))
        max_tests = min(int(parameters.get('maximum_tests_per_order', len(codes))), len(codes))
        if min_tests < 1 or max_tests < min_tests:
            raise ValueError('Test-count range must include at least one available test.')
        for index in range(count):
            customer = Customer(hn=f'DEMO-{batch_id}-{index + 1}',
                                firstname=f'[DEMO] Simulated Patient {index + 1}', lastname='',
                                gender='Unknown', dob=date(2000, 1, 1),
                                address='Synthetic demonstration patient; DOB is a placeholder (2000-01-01).')
            order = LabOrder(customer=customer, order_datetime=generated_at + timedelta(hours=offsets[index]),
                             priority=priorities[index], comment=f'DEMONSTRATION: batch {batch_id}; scenario {source.scenario.name}.')
            # Each order receives a seeded random subset of tests the workflow
            # can perform. Every analyzer node declares its supported assays;
            # the configurable range represents which were ordered.
            test_count = test_rng.randint(min_tests, max_tests)
            requested = sorted(test_rng.sample(codes, test_count))
            order.order_items = [LabOrderItem(test=catalogue[code]) for code in requested]
            session.add(LISDemonstrationOrder(batch_id=batch_id, scenario_id=source.scenario_id,
                        workflow_id=source.choice.document.workflow_id, generated_at=generated_at, order=order))
    return ScenarioLISOrderSource(source.choice, source.scenario_id, batch_id=batch_id,
                                  database_engine=source.database_engine)


@login_required
def choose_simulation_source(*, parent=None):
    """Select a scenario only; loading orders is a separate one-click action."""
    import FreeSimpleGUI as sg
    from app.ui_styles import create_window
    from app.system.lis_analysis import analysis_workflow_choices
    choices = {}
    for choice in analysis_workflow_choices():
        for scenario in choice.document.workflow.scenarios:
            choices[f'{scenario.name} — {choice.label}'] = (choice, scenario)
    if not choices:
        sg.popup_error('Create a workflow with a scenario before loading simulated orders.', keep_on_top=True)
        return None
    default = next((label for label, (choice, scenario) in choices.items()
                    if scenario is choice.document.workflow.active_scenario), next(iter(choices)))
    layout = [[sg.Text('Choose scenario', font=('Arial', 16, 'bold'))],
              [sg.Combo(list(choices), default_value=default, key='scenario', readonly=True, size=(65, 1))],
              [sg.Text('Then click Load Simulated Orders to generate a fresh batch with real LIS tests.')],
              [sg.Button('Select'), sg.Button('Cancel')]]
    window = create_window('Simulation order scenario', layout, modal=True, parent=parent, always_on_top=True)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, 'Cancel'):
                return None
            if event == 'Select' and values.get('scenario') in choices:
                choice, scenario = choices[values['scenario']]
                return ScenarioLISOrderSource(choice, scenario.scenario_id)
    finally:
        window.close()
