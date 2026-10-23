"""Explicitly copy saved simulation orders into the operational LIS for teaching."""
from dataclasses import dataclass
from datetime import date, datetime
import json

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.system.models import (Customer, LabOrder, LabOrderItem, SimulationOrder,
                               SimulationRun, SimulationLISImport, Test, engine)
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


def import_simulation_orders(run_id, *, test_mapping=None, database_engine=None):
    """One transaction; repeat imports retain existing orders and their LIS state."""
    mapping = test_mapping or {}
    with Session(database_engine or engine) as session, session.begin():
        if session.get(SimulationRun, run_id) is None:
            raise ValueError('The simulation run no longer exists.')
        orders = list(session.scalars(select(SimulationOrder).where(SimulationOrder.run_id == run_id)
                                      .options(selectinload(SimulationOrder.patient)).order_by(SimulationOrder.id)))
        if not orders:
            raise ValueError('This simulation run has no saved orders.')
        imported = set(session.scalars(select(SimulationLISImport.simulation_order_id)
                                      .join(SimulationOrder).where(SimulationOrder.run_id == run_id)))
        tests = {test.code: test for test in session.scalars(select(Test).where(Test.active.is_(True)))}
        prepared = []
        for order in orders:
            if order.id in imported:
                continue
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
                             comment=f'DEMONSTRATION: simulation run {source.run_id}, order {source.order_id}.')
            order.order_items = [LabOrderItem(test=tests[code]) for code in codes]
            session.add(SimulationLISImport(simulation_order_id=source.id, order=order,
                                            imported_at=datetime.now()))
        return ImportSummary(len(prepared), len(imported), run_id)


class SimulationLISOrderSource(SQLiteOrderSource):
    """Normal editable LIS orders, filtered to one imported simulation run."""
    label = 'Simulation'

    def __init__(self, run_id, *, database_engine=None):
        self.run_id = run_id
        self.database_engine = database_engine or engine

    def list_orders(self):
        from app.system.models import LabOrderItem
        with Session(self.database_engine) as session:
            query = (select(LabOrder).join(SimulationLISImport)
                     .join(SimulationOrder, SimulationOrder.id == SimulationLISImport.simulation_order_id)
                     .where(SimulationOrder.run_id == self.run_id)
                     .options(selectinload(LabOrder.customer), selectinload(LabOrder.doctor),
                              selectinload(LabOrder.order_items).selectinload(LabOrderItem.test))
                     .order_by(LabOrder.order_datetime, LabOrder.id))
            return list(session.scalars(query))

    def get_order(self, order_id):
        return next((order for order in self.list_orders() if str(order.id) == str(order_id)), None)


@login_required
def choose_simulation_source(*, parent=None):
    """Choose saved teaching data, map unknown tests, and explicitly import it."""
    import FreeSimpleGUI as sg
    from app.ui_styles import create_window, DANGER_BUTTON_COLOR
    from sqlalchemy.exc import SQLAlchemyError
    with Session(engine) as session:
        runs = list(session.scalars(select(SimulationRun).order_by(SimulationRun.started_at.desc())))
        choices = {f'{run.scenario_name} · {run.started_at:%Y-%m-%d %H:%M} [{run.id[:8]}]': run.id
                   for run in runs}
    if not choices:
        sg.popup_error('No saved simulation runs are available.', keep_on_top=True)
        return None
    layout = [[sg.Text('Simulation orders for LIS demonstration', font=('Arial', 16, 'bold'))],
              [sg.Text('Saved run'), sg.Combo(list(choices), default_value=next(iter(choices)),
                                             key='run', readonly=True, size=(55, 1))],
              [sg.Text('Creates normal LIS orders and synthetic customers in the current database.\n'
                       'Existing imports are reused. Original simulation records are retained.')],
              [sg.Button('Next'), sg.Button('Cancel')]]
    window = create_window('Choose Simulation Run', layout, modal=True, parent=parent, always_on_top=True)
    try:
        event, values = window.read()
        if event != 'Next':
            return None
        run_id = choices.get(values.get('run'))
        if run_id is None:
            return None
    finally:
        window.close()
    with Session(engine) as session:
        orders = list(session.scalars(select(SimulationOrder).where(SimulationOrder.run_id == run_id)))
        imported = set(session.scalars(select(SimulationLISImport.simulation_order_id)
                                      .join(SimulationOrder).where(SimulationOrder.run_id == run_id)))
        test_codes = list(session.scalars(select(Test.code).where(Test.active.is_(True)).order_by(Test.code)))
        try:
            missing = sorted({code for order in orders if order.id not in imported
                              for code in requested_codes(order)} - set(test_codes))
        except ValueError as exc:
            sg.popup_error(str(exc), keep_on_top=True)
            return None
    mapping_rows = [[sg.Text(code, size=(20, 1)), sg.Combo(test_codes, readonly=True, key=f'test:{index}', size=(30, 1))]
                    for index, code in enumerate(missing)]
    layout = [[sg.Text(f'{len(orders)} saved orders · {len(imported)} already imported')],
              [sg.Text('Map unknown simulation codes to active LIS tests.' if missing else 'All requested tests match the LIS catalogue.')],
              [sg.Column(mapping_rows, scrollable=True, vertical_scroll_only=True, size=(520, 220))] if missing else [],
              [sg.Text('New orders start pending, without results or approvals.\n'
                       'Patients are marked [DEMO]; missing demographics use Unknown gender and DOB 2000-01-01.')],
              [sg.Button('Import as LIS orders'), sg.Button('Cancel', button_color=DANGER_BUTTON_COLOR)]]
    window = create_window('Import Simulation Orders', layout, modal=True, parent=parent, always_on_top=True)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, 'Cancel'):
                return None
            if event == 'Import as LIS orders':
                mapping = {code: values.get(f'test:{index}') for index, code in enumerate(missing)}
                if any(value not in test_codes for value in mapping.values()):
                    sg.popup_error('Choose an active LIS test for every simulation code.', keep_on_top=True)
                    continue
                try:
                    summary = import_simulation_orders(run_id, test_mapping=mapping)
                except (ValueError, SQLAlchemyError) as exc:
                    sg.popup_error(f'No orders were imported: {exc}', keep_on_top=True)
                    continue
                sg.popup_ok(f'{summary.created} LIS orders created; {summary.skipped} existing imports reused.', keep_on_top=True)
                return SimulationLISOrderSource(run_id)
    finally:
        window.close()
