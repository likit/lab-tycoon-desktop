"""Read-only laboratory counts and median turnaround times, in minutes."""
from copy import deepcopy
from statistics import median

import FreeSimpleGUI as sg
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.system.models import LabOrder, engine
from app.ui_styles import create_window, DANGER_BUTTON_COLOR


PHASES = ('Pre-analytical', 'Analytical', 'Post-analytical', 'Lab TAT')


def summarize(records):
    """Each record supplies test count, exclusion flag and four ordered milestones."""
    samples = {phase: [] for phase in PHASES}
    for record in records:
        if record['excluded']:
            continue
        times = record['times']
        # Require consistent chronological milestones; never turn negative TAT into zero.
        if any(time is None for time in times) or not all(a <= b for a, b in zip(times, times[1:])):
            continue
        for phase, duration in zip(PHASES, (times[1]-times[0], times[2]-times[1],
                                           times[3]-times[2], times[3]-times[0])):
            samples[phase].append(duration)
    return dict(orders=len(records), tests=sum(r['tests'] for r in records),
                excluded=sum(r['excluded'] for r in records),
                medians={phase: median(values) if values else None for phase, values in samples.items()},
                sample_size=len(samples['Lab TAT']))


def lis_performance(database_engine=None):
    records = []
    with Session(database_engine or engine) as session:
        orders = session.scalars(select(LabOrder).options(selectinload(LabOrder.order_items)))
        for order in orders:
            items = [i for i in order.order_items if i.cancelled_at is None]
            end = max((i.finished_at for i in items), default=None) if items and all(i.finished_at for i in items) else None
            dates = (order.order_datetime, order.received_at, end, order.approved_at)
            origin = order.order_datetime
            times = [((dt-origin).total_seconds()/60 if dt is not None and origin is not None else None)
                     for dt in dates]
            records.append(dict(tests=len(order.order_items),
                excluded=order.rejected_at is not None or order.cancelled_at is not None, times=times))
    return summarize(records)


def simulation_performance(live):
    records = []
    if live is None:
        return summarize(records)
    with live.condition:
        specimens = [(deepcopy(s.metadata), deepcopy(s.history)) for s in live.engine.specimens]
        nodes = {node.widget.name: node.widget.node_type for node in live.engine.workflow.graph.iter_nodes()}
    for metadata, history in specimens:
        # Future specimens are allocated before arrival; count only actual arrivals.
        arrivals = [e['details']['time'] for e in history if e['event'] in ('order_created', 'arrived')
                    and 'time' in e.get('details', {})]
        if not arrivals:
            continue
        starts = [e['details']['time'] for e in history if e['event'] == 'test_started']
        finishes = [e['details']['time'] for e in history if e['event'] == 'test_finished']
        approvals = [e['details']['time'] for e in history if e['event'] == 'departed'
                     and nodes.get(e['widget']) in ('result validating', 'result validation')]
        times = [min(arrivals), min(starts) if starts else None,
                 max(finishes) if finishes else None, max(approvals) if approvals else None]
        records.append(dict(tests=len(metadata.get('all_tests', [])),
            excluded=bool(metadata.get('rejected')), times=[t*60 if t is not None else None for t in times]))
    return summarize(records)


def create_lab_performance_window(project, *, live=None, parent=None):
    sources = ['LIS database', 'Current floor run']
    source = sources[1] if live is not None else sources[0]
    layout = [[sg.Text('Lab Performance', font=('Arial', 22, 'bold'))],
              [sg.Text(project.name, font=('Arial', 14, 'bold'))],
              [sg.Text('Data source'), sg.Combo(sources, default_value=source, key='source',
                                               readonly=True, enable_events=True), sg.Button('Refresh')],
              [sg.Text('', key='scope', size=(78, 2))],
              [sg.Text('', key='counts', font=('Arial', 17, 'bold'))],
              [sg.Table(values=[], headings=['Phase', 'Median (minutes)'], key='tat',
                        num_rows=4, auto_size_columns=False, col_widths=[24, 22], justification='left')],
              [sg.Text('', key='samples', size=(78, 2))],
              [sg.Text('', key='definitions', size=(78, 4))], [sg.Button('Close', button_color=DANGER_BUTTON_COLOR)]]
    window = create_window(f'Lab Performance — {project.name}', layout,
                           parent=parent, independent=True, resizable=True, finalize=True)
    def refresh():
        is_live = source == sources[1]
        data = simulation_performance(live) if is_live else lis_performance()
        window['scope'].update('Current floor run only; simulated time.' if is_live else
            'All LIS orders in the selected database, including demonstration orders. Not filtered by project.')
        window['counts'].update(f"Orders: {data['orders']}    Tests: {data['tests']}    Rejected/cancelled: {data['excluded']}")
        window['tat'].update(values=[[phase, '—' if data['medians'][phase] is None else f"{data['medians'][phase]:.2f}"] for phase in PHASES])
        window['samples'].update(f"TAT sample: {data['sample_size']} orders with complete, chronological milestones.\n"
                                 'Rejected/cancelled and incomplete orders excluded from medians. Counts include them.')
        window['definitions'].update(
            'Pre: arrival → first test starts. Analytical: first test starts → last test finishes.\n'
            'Post: last test finishes → validation completes. Lab: arrival → validation completes.' if is_live else
            'Pre: ordered → received. Analytical: received → last active test finishes.\n'
            'Post: last active test finishes → order approved. Lab: ordered → order approved.')
    try:
        refresh()
        while True:
            event, values = window.read(timeout=1000)
            if event in (sg.WIN_CLOSED, 'Close'):
                break
            if event == 'source':
                source = values['source']
            if event in ('source', 'Refresh') or source == sources[1]:
                refresh()
    finally:
        window.close()
