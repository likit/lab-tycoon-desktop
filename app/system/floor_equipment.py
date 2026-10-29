"""Edit workflow-local equipment settings directly from the laboratory floor."""
import FreeSimpleGUI as sg
from app.system.storage import WorkflowDocument
from app.system.workflow_editor import (WorkflowEditorController, _show_equipment_node_dialog,
                                        _show_node_form_dialog)
from app.ui_styles import create_window


def edit_floor_equipment(project, workflow, equipment_id, *, parent=None):
    nodes = [n for n in workflow.graph.iter_nodes()
             if n.metadata.get('equipment_id') == equipment_id
             or n.metadata.get('attend_equipment_id') == equipment_id]
    if not nodes:
        sg.popup_ok('This equipment has no settings in the selected workflow.\n'
                    'Connect it in Edit Workflow first.', title='Edit Equipment', keep_on_top=True)
        return False
    node = nodes[0]
    if len(nodes) > 1:
        labels = {f'{n.widget.name} [{n.node_id}]': n for n in nodes}
        dialog = create_window('Choose Equipment Step', [
            [sg.Text('This station is used by multiple steps. Choose settings to edit:')],
            [sg.Combo(list(labels), default_value=next(iter(labels)), key='step', readonly=True, size=(55, 1))],
            [sg.Button('Edit'), sg.Button('Cancel')]], parent=parent, modal=True)
        try:
            event, values = dialog.read()
            if event != 'Edit':
                return False
            node = labels[values['step']]
        finally:
            dialog.close()
    controller = WorkflowEditorController(
        current_document=WorkflowDocument('floor', 'Floor workflow', workflow),
        on_change=project.mark_dirty, equipment_instances=project.floor.equipment_instances)
    controller.selected_node_id = node.node_id
    if node.kind == 'equipment':
        settings = _show_equipment_node_dialog(controller, node)
        if settings is None:
            return False
        controller.bind_selected_equipment(**settings)
    else:
        settings = _show_node_form_dialog(node, controller.equipment_choices(), controller.configure_node_staff,
                                         active_scenario_name=workflow.active_scenario.name if workflow.active_scenario else None)
        if settings is None:
            return False
        controller.update_node_from_form(node.node_id, **settings)
    return True
