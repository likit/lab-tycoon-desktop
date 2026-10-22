"""Resolve physical equipment capabilities without changing editor node kinds."""
from app.system.model import Workflow


def resolve_equipment_workflow(workflow: Workflow, equipment_types=None) -> Workflow:
    """Use analyzer processing for generic nodes bound to chemistry analyzers.

    Resolve a run-local copy so legacy projects need no edits or resaving. Explicit
    manual/automated analyzer choices and non-analyzer equipment stay unchanged.
    """
    equipment_types = equipment_types or {}
    resolved = {}
    for node in workflow.graph.iter_nodes():
        kind = equipment_types.get(node.metadata.get('equipment_id'),
                                   node.metadata.get('equipment_type'))
        if (node.kind == 'equipment' and node.widget.node_type == 'equipment'
                and kind == 'chemistry_analyzer'):
            resolved[node.node_id] = kind
    if not resolved:
        return workflow
    copied = Workflow.from_dict(workflow.to_dict())
    for node_id, kind in resolved.items():
        node = copied.get_node(node_id)
        node.metadata.update(equipment_type=kind, node_type='automated analyzer')
        node.widget.metadata.update(equipment_type=kind, node_type='automated analyzer')
    return copied
