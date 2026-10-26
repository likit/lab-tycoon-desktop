"""Persist source selection without persisting uploaded patient data in settings."""
from pathlib import Path
import json

from app.config import config_dict, update_config_yaml
from app.system.order_sources import (SQLiteOrderSource, InMemoryOrderSource, set_active_order_source,
                                      load_uploaded_order_source, use_uploaded_order_source)


def remember_order_source(source):
    from app.system.simulation_lis import ScenarioLISOrderSource, SimulationLISOrderSource
    if isinstance(source, ScenarioLISOrderSource):
        selection = dict(kind='scenario', workflow_key=source.choice.key,
                         scenario_id=source.scenario_id, workflow=json.loads(json.dumps(source.choice.document.to_dict())),
                         project_id=source.choice.project_id, label=source.choice.label)
    elif isinstance(source, SimulationLISOrderSource):
        selection = dict(kind='saved_run', run_id=source.run_id, batch_id=source.batch_id)
    elif isinstance(source, InMemoryOrderSource):
        selection = dict(kind='uploaded', path=str(Path(source.source_path).resolve()))
    else:
        selection = dict(kind='sqlite')
    update_config_yaml(order_source_selection=selection)


def restore_order_source():
    """Restore once on app startup; retain unavailable preferences for recovery."""
    from app.system.simulation_lis import ScenarioLISOrderSource, SimulationLISOrderSource
    from app.system.lis_analysis import analysis_workflow_choices, AnalysisWorkflowChoice
    from app.system.storage import WorkflowDocument
    selection = config_dict.get('order_source_selection') or {}
    try:
        kind = selection.get('kind', 'sqlite')
        if kind == 'scenario':
            choice = next((choice for choice in analysis_workflow_choices()
                           if choice.key == selection['workflow_key']), None)
            if choice is None:
                document = WorkflowDocument.from_dict(selection['workflow'])
                choice = AnalysisWorkflowChoice(selection['workflow_key'], selection['label'], document,
                                                selection.get('project_id'))
            source = ScenarioLISOrderSource(choice, selection['scenario_id'])
            set_active_order_source(source)
        elif kind == 'uploaded':
            load_uploaded_order_source(selection['path'])
            use_uploaded_order_source()
        elif kind == 'saved_run':
            set_active_order_source(SimulationLISOrderSource(selection['run_id'], batch_id=selection.get('batch_id')))
        else:
            set_active_order_source(SQLiteOrderSource())
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
        set_active_order_source(SQLiteOrderSource())
        return f'The saved order source is unavailable; using SQLite for this session. {exc}'
    return None
