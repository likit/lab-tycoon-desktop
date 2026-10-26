"""Execute LIS test selections through the real SimPy workflow engine."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.system.models import LabOrder, LabOrderItem, LISWorkflowAnalysis, User, engine
from app.system.model import Workflow
from app.system.storage import WorkflowDocument, WorkflowRepository
from app.system.simulation import WorkflowSimulationEngine, WorkflowSimulationSettings
from app.system.simulation.lab import WorkflowRouter, _build_specimens_from_orders
from app.system.equipment_workflow import resolve_equipment_workflow
from app.system.order_sources import InMemoryOrder, InMemoryCustomer, InMemoryOrderItem, InMemoryTest


@dataclass(frozen=True)
class AnalysisSummary:
    run_id: str
    analyzed: int
    skipped: int
    makespan: float
    workflow_name: str
    reported: int = 0
    approved: int = 0


def pending_item_ids_for_orders(order_ids, *, database_engine=None):
    """Limit bulk analysis to eligible work in exactly the displayed/selected orders."""
    if not order_ids:
        return []
    with Session(database_engine or engine) as session:
        return list(session.scalars(select(LabOrderItem.id).join(LabOrder).where(
            LabOrder.id.in_(order_ids), LabOrderItem.finished_at.is_(None),
            LabOrderItem.cancelled_at.is_(None), LabOrderItem.reported_at.is_(None),
            LabOrderItem.approved_at.is_(None), LabOrder.cancelled_at.is_(None),
            LabOrder.rejected_at.is_(None), LabOrder.approved_at.is_(None)
        ).order_by(LabOrderItem.id)))


@dataclass(frozen=True)
class AnalysisWorkflowChoice:
    key: str
    label: str
    document: WorkflowDocument
    project_id: str | None = None


def analysis_workflow_choices():
    """Include current project workflows and saved standalone workflows."""
    from app.project import project_session
    project = project_session.active_project
    choices = []
    if project is not None:
        equipment_types = {item.instance_id: item.equipment_type for item in project.floor.equipment_instances}
        for selected in project.workflows:
            workflow = resolve_equipment_workflow(Workflow.from_dict(selected.workflow.to_dict()), equipment_types)
            for node in workflow.graph.iter_nodes():
                equipment_type = equipment_types.get(node.metadata.get('equipment_id'))
                if equipment_type:
                    node.metadata['equipment_type'] = equipment_type
            document = WorkflowDocument(selected.workflow_id, selected.name, workflow)
            choices.append(AnalysisWorkflowChoice(f'project:{project.project_id}:{selected.workflow_id}',
                f'{selected.name} — {project.name} [{selected.workflow_id[:8]}]', document, project.project_id))
    for document in WorkflowRepository().list_documents():
        choices.append(AnalysisWorkflowChoice(f'saved:{document.workflow_id}',
            f'{document.name} — Saved [{document.workflow_id[:8]}]', document))
    return choices


def default_analysis_workflow(choices):
    from app.config import config_dict
    if not choices:
        raise ValueError('Create or open a workflow before analyzing tests.')
    remembered = config_dict.get('default_analysis_workflow_key')
    return next((choice for choice in choices if choice.key == remembered),
                next((choice for choice in choices
                      if choice.document.workflow_id == config_dict.get('active_workflow_id')
                      and choice.project_id == config_dict.get('active_project_id')), choices[0]))


def remember_analysis_workflow(choice):
    from app.config import update_config_yaml
    update_config_yaml(default_analysis_workflow_key=choice.key,
                       active_workflow_id=choice.document.workflow_id, active_project_id=choice.project_id)


def active_analysis_workflow():
    return default_analysis_workflow(analysis_workflow_choices()).document


def reception_node(workflow):
    router = WorkflowRouter()
    reachable = set(router.reachable_node_ids(workflow, router.start_node_id(workflow)))
    nodes = [node for node in workflow.graph.iter_nodes() if node.node_id in reachable and node.widget.enabled]
    candidates = [node for node in nodes if node.widget.node_type == 'specimen registration']
    if not candidates:
        candidates = [node for node in nodes if node.metadata.get('equipment_type') == 'specimens_reception_station']
    if len(candidates) != 1:
        raise ValueError('The active workflow must have one reachable reception/Specimen Registration node.')
    if not workflow.graph.outgoing_edges(candidates[0].node_id):
        raise ValueError('Connect reception to the remaining workflow before analyzing tests.')
    return candidates[0]


def result_stage(node):
    """Identify processing stages, excluding the visual reporting endpoint."""
    if node.metadata.get('terminal_output'):
        return None
    node_type = node.widget.node_type.strip().casefold()
    if node_type in ('result reporting', 'reporting'):
        return 'reporter'
    if node_type in ('result validating', 'result validation', 'validating', 'validation'):
        return 'approver'
    if node_type == 'specimens processing':
        return {'reporting': 'reporter', 'validating': 'approver', 'validation': 'approver'}.get(
            node.widget.name.strip().casefold())
    return None


def analyze_lis_items(item_ids, *, workflow_document, operator_username, database_engine=None,
                      random_seed=None, simulation_runner=None):
    """Group pending selected tests by order; commit results and trace on success."""
    ids = list(dict.fromkeys(int(item_id) for item_id in item_ids))
    if not ids:
        raise ValueError('Select at least one test to analyze.')
    workflow = resolve_equipment_workflow(Workflow.from_dict(workflow_document.workflow.to_dict()))
    reception = reception_node(workflow)
    scenario = workflow.active_scenario
    if scenario is None:
        raise ValueError('Select an active scenario for this workflow.')
    run_id = uuid4().hex
    with Session(database_engine or engine) as session, session.begin():
        operator = session.scalar(select(User).where(User.username == operator_username, User.active.is_(True)))
        if operator is None:
            raise ValueError('An active signed-in user is required to analyze tests.')
        items = list(session.scalars(select(LabOrderItem).where(LabOrderItem.id.in_(ids))
                     .options(selectinload(LabOrderItem.test), selectinload(LabOrderItem.order))
                     .order_by(LabOrderItem.id)))
        if len(items) != len(ids):
            raise ValueError('One or more selected tests no longer exist.')
        pending = [item for item in items if not (item.finished_at or item.reported_at or item.approved_at
                   or item.cancelled_at or item.order.cancelled_at or item.order.rejected_at or item.order.approved_at)]
        if not pending:
            raise ValueError('No pending eligible tests remain to analyze.')
        if any(not item.test.active for item in pending):
            raise ValueError('A selected test is inactive in the LIS catalogue.')
        started_at = max([datetime.now()] + [item.order.received_at or item.order.order_datetime or datetime.min
                                           for item in pending])
        pending_by_order = {}
        for item in pending:
            pending_by_order.setdefault(item.order_id, []).append(item)
        # Use the simulation menu's order-to-specimen conversion: reception,
        # staff and analyzer slots see one specimen carrying its selected tests.
        orders = []
        for order_id, order_items in pending_by_order.items():
            order = order_items[0].order
            orders.append(InMemoryOrder(
                id=str(order_id), customer_id=str(order.customer_id),
                order_datetime=order.order_datetime or started_at, priority=order.priority,
                customer=InMemoryCustomer(str(order.customer_id), '', ''),
                order_items=[InMemoryOrderItem(InMemoryTest(item.test.code)) for item in order_items],
                source='lis'))
        specimens = _build_specimens_from_orders(orders)
        for specimen in specimens:
            order = pending_by_order[int(specimen.metadata['order_id'])][0].order
            specimen.metadata.update(entry_node_id=reception.node_id,
                                     skip_entry_node=order.received_at is not None)
        settings = WorkflowSimulationSettings.from_scenario(specimens_number=len(specimens), workflow=workflow,
                    scenario=scenario, random_seed=random_seed)
        # These are existing selected tests, not newly generated order arrivals.
        settings.interarrival_time = 0
        stage_events = {}
        stage_started = set()

        def record_stage(event):
            node = workflow.get_node(event['node_id'])
            role = result_stage(node)
            if role is None:
                return
            key = (event['specimen_id'], event['node_id'])
            if event['event'] == 'started':
                stage_started.add(key)
            elif event['event'] == 'finished' and key in stage_started:
                stage_started.remove(key)
                stage_events.setdefault(event['specimen_id'], []).append(
                    dict(node_id=node.node_id, role=role, time=event['time']))

        simulator = WorkflowSimulationEngine(workflow, settings=settings, scenario=scenario,
                    specimens=specimens, workflow_name=workflow_document.name, on_activity=record_stage)
        result = simulation_runner(simulator) if simulation_runner is not None else simulator.run()
        required_roles = {event['role'] for events in stage_events.values() for event in events}
        for role in sorted(required_roles):
            if not operator.has_role(role):
                raise ValueError(f'Workflow completion requires the {role} role. No LIS changes saved.')
        completed = {specimen.specimen_id: specimen for specimen in result.completed_specimens}
        receipt_times = {}
        for item in pending:
            specimen = completed.get(str(item.order_id))
            if specimen is None or item.test.code not in specimen.metadata.get('completed_tests', []):
                raise ValueError(f'Test {item.test.code} did not complete on an analyzer. No LIS changes saved.')
            test_events = [event['details']['time'] for event in specimen.history
                           if event['event'] == 'test_finished'
                           and event.get('details', {}).get('test_code') == item.test.code]
            if not test_events:
                raise ValueError(f'Test {item.test.code} has no analyzer completion event.')
            if item.order.received_at is None:
                receipt_events = [event['details']['time'] for event in specimen.history
                                  if event.get('widget') == reception.widget.name and event['event'] == 'departed']
                if not receipt_events:
                    raise ValueError('Reception did not complete. No LIS changes saved.')
                receipt_times[item.order_id] = min(receipt_times.get(item.order_id, float('inf')), min(receipt_events))
            item.finished_at = started_at + timedelta(hours=max(test_events))
            item.updated_at = item.finished_at
            item.updater = operator
            if item._value is None:
                item.random_value()
            events = stage_events.get(specimen.specimen_id, [])
            reports = [event['time'] for event in events if event['role'] == 'reporter']
            approvals = [event['time'] for event in events if event['role'] == 'approver']
            if reports:
                report_time = min(reports)
                if report_time < max(test_events):
                    raise ValueError('Reporting completed before analysis. No LIS changes saved.')
                if item._value is None or item._value == '':
                    raise ValueError(f'Test {item.test.code} has no result to report. No LIS changes saved.')
                item.reported_at = started_at + timedelta(hours=report_time)
                item.reporter = operator
                item.updated_at = item.reported_at
            if approvals:
                approval_time = min(approvals)
                approved_at = started_at + timedelta(hours=approval_time)
                if item.reported_at is None or approved_at < item.reported_at:
                    raise ValueError('Validation requires a previously reported result. No LIS changes saved.')
                item.approved_at = approved_at
                item.approver = operator
                item.updated_at = item.approved_at
            item.comment = ((item.comment + '\n') if item.comment else '') + f'Simulated workflow analysis: {run_id}'
        for item in pending:
            if item.order_id in receipt_times:
                item.order.received_at = started_at + timedelta(hours=receipt_times[item.order_id])
                item.order.receiver = operator
        for order in {item.order for item in pending if item.approved_at is not None}:
            active_items = [item for item in order.order_items if item.cancelled_at is None]
            if active_items and all(item.approved_at is not None for item in active_items):
                order.approved_at = max(item.approved_at for item in active_items)
                order.approver = operator
        session.add(LISWorkflowAnalysis(id=run_id, started_at=started_at, operator_id=operator.id,
                    workflow_name=workflow_document.name, scenario_id=scenario.scenario_id,
                    workflow_json=json.dumps(workflow.to_dict()),
                    trace_json=json.dumps([{'item_id': str(item.id), 'order_id': str(item.order_id),
                                           'specimen_id': str(item.order_id), 'test_code': item.test.code,
                                           'history': completed[str(item.order_id)].history,
                                           'stage_events': stage_events.get(str(item.order_id), [])}
                                          for item in pending], default=str),
                    item_ids_json=json.dumps([item.id for item in pending]), makespan=result.makespan))
        return AnalysisSummary(run_id, len(pending), len(items) - len(pending), result.makespan, workflow_document.name,
                               reported=sum(item.reported_at is not None for item in pending),
                               approved=sum(item.approved_at is not None for item in pending))
