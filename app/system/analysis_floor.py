"""Adapt selected LIS work to the shared live simulation and floor window."""
from copy import deepcopy

from app.project import LabTycoonProject, ProjectFloor, ProjectEquipmentInstance
from app.system.model import Workflow
from app.system.floor_view import FloorPlan, EQUIPMENT_CATALOG
from app.system.live_simulation import LiveSimulationSession, DEFAULT_SIMULATION_SPEED
from app.system.lis_analysis import analyze_lis_items


class AnalysisCancelled(ValueError):
    """Cancelling a live analysis rolls back the surrounding LIS transaction."""


def analysis_floor(workflow, project=None):
    """Reuse the project floor; add unplaced stages only to a temporary layout."""
    visual = Workflow.from_dict(workflow.to_dict())
    floor = deepcopy(project.floor) if project is not None else ProjectFloor()
    instances = {item.instance_id: item for item in floor.equipment_instances}
    start_column = floor.columns + 2 if instances else 2
    added = 0
    for node in visual.graph.iter_nodes():
        equipment_id = node.metadata.get('equipment_id') or node.metadata.get('attend_equipment_id')
        if equipment_id in instances:
            node.metadata['equipment_id'] = equipment_id
            if node.kind != 'equipment':
                node.kind = 'equipment'
                node.metadata.setdefault('output_specimens', True)
            continue
        node_type = node.widget.node_type.strip().casefold()
        equipment_type = node.metadata.get('equipment_type')
        if not equipment_type:
            if node_type == 'specimen registration':
                equipment_type = 'specimens_reception_station'
            elif node_type in ('automated analyzer', 'manual analyzer'):
                equipment_type = 'chemistry_analyzer'
            elif node_type in ('result reporting', 'result validating', 'result validation') or node.metadata.get('terminal_output'):
                equipment_type = 'reporting_station'
            elif node_type == 'specimens processing':
                equipment_type = ('reporting_station' if node.widget.name.strip().casefold()
                                  in ('reporting', 'validating', 'validation') else 'centrifuge')
        if equipment_type not in EQUIPMENT_CATALOG:
            continue
        if not equipment_id:
            compatible = [item for item in instances.values() if item.equipment_type == equipment_type]
            if project is not None and len(compatible) == 1:
                node.metadata['equipment_id'] = compatible[0].instance_id
                node.kind = 'equipment'
                node.metadata.setdefault('output_specimens', True)
                continue
        equipment_id = equipment_id or f'analysis-{node.node_id}'
        if equipment_id not in instances:
            row, col = 2 + (added // 2) * 4, start_column + (added % 2) * 4
            instance = ProjectEquipmentInstance(equipment_id, equipment_type, row, col)
            floor.equipment_instances.append(instance)
            instances[equipment_id] = instance
            floor.rows = max(floor.rows, row + 4)
            floor.columns = max(floor.columns, col + 4)
            added += 1
        node.kind = 'equipment'
        node.metadata.update(equipment_id=equipment_id, output_specimens=True)
    return visual, FloorPlan(floor_state=floor)


class LISAnalysisSession(LiveSimulationSession):
    """Use the common worker loop, saving selected LIS items after completion."""
    is_lis_analysis = True

    def __init__(self, item_ids, *, workflow_document, operator_username, project=None,
                 database_engine=None, random_seed=None, speed=DEFAULT_SIMULATION_SPEED):
        self.item_ids = list(dict.fromkeys(item_ids))
        self.document = workflow_document
        self.operator_username = operator_username
        self.database_engine = database_engine
        self.random_seed = random_seed
        self.prepared = False
        workflow, floor = analysis_floor(workflow_document.workflow, project)
        super().__init__(workflow_document.workflow, display_workflow=workflow,
            count=len(self.item_ids), seed=random_seed, speed=speed,
            equipment_types={item.instance_id: item.equipment_type for item in floor.equipment_instances})
        self.floor_project = LabTycoonProject.create(project.name if project else workflow_document.name)
        self.floor_project.floor = floor.floor_state
        self.floor_project.workflow = self.workflow
        self.floor_project.workflow_name = workflow_document.name
        self.floor_project.live_simulation = self

    def _run_selected(self, engine):
        with self.condition:
            self._bind_engine(engine)
            previous = engine.on_activity
            def activity(event):
                if previous is not None:
                    previous(event)
                self._activity(event)
            engine.on_activity = activity
            self.prepared = True
        try:
            result = self._execute_engine()
            if result is None:
                raise AnalysisCancelled('Analysis cancelled. No LIS changes saved.')
            return result
        finally:
            engine.on_activity = previous

    def _run(self):
        try:
            summary = analyze_lis_items(self.item_ids, workflow_document=self.document,
                operator_username=self.operator_username, database_engine=self.database_engine,
                random_seed=self.random_seed, simulation_runner=self._run_selected)
            with self.condition:
                self.result = summary
                self.state = 'completed'
        except AnalysisCancelled:
            with self.condition:
                self.state = 'cancelled'
        except Exception as exc:
            with self.condition:
                self.error = str(exc)
                self.state = 'failed'
