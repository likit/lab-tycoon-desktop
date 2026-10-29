"""A small, self-contained laboratory for trying the simulation."""
from app.project import LabTycoonProject, ProjectEquipmentInstance


def create_demo_project(name="Demo Laboratory", *, analyzer_tests=None):
    project = LabTycoonProject.create(
        name, 'One staff member receives, analyzes, reports, and validates demo orders.')
    project.floor.equipment_instances = [
        ProjectEquipmentInstance('RECP-001', 'specimens_reception_station', 3, 2),
        ProjectEquipmentInstance('CENT-001', 'centrifuge', 3, 4),
        ProjectEquipmentInstance('CHEM-001', 'chemistry_analyzer', 3, 7),
        ProjectEquipmentInstance('REPORT-001', 'reporting_station', 6, 7),
    ]
    workflow = project.workflow
    source = workflow.create_node('Demo Orders', kind='order_adapter',
        metadata={'node_type': 'order adapter', 'adapter_type': 'simulation'})
    reception = workflow.create_connected_node(source.node_id, 'Specimen Registration',
        metadata={'node_type': 'specimen registration', 'required_staff': 1,
                  'human_throughput': 60, 'attend_equipment_id': 'RECP-001'})
    centrifuge = workflow.create_connected_node(reception.node_id, 'Centrifuge', 12,
        kind='equipment', metadata={'node_type': 'equipment',
            'equipment_id': 'CENT-001', 'equipment_type': 'centrifuge',
            'throughput': 12, 'human_throughput': 60, 'required_staff': 1,
            'capacity': 1, 'output_specimens': True})
    analyzer = workflow.create_connected_node(centrifuge.node_id, 'Chemistry Analyzer', 20,
        kind='equipment', metadata={'node_type': 'automated analyzer',
            'equipment_id': 'CHEM-001', 'equipment_type': 'chemistry_analyzer',
            'analyzer_tests': list(analyzer_tests) if analyzer_tests is not None else ['TG'], 'throughput': 20,
            'required_staff': 1, 'human_throughput': 60,
            'capacity': 1, 'output_data': True})
    reporting = workflow.create_connected_node(analyzer.node_id, 'Result Reporting',
        metadata={'node_type': 'result reporting', 'required_staff': 1,
                  'human_throughput': 60, 'attend_equipment_id': 'REPORT-001'})
    validation = workflow.create_connected_node(reporting.node_id, 'Result Validation',
        metadata={'node_type': 'result validating', 'required_staff': 1,
                  'human_throughput': 60, 'attend_equipment_id': 'REPORT-001'})
    workflow.create_connected_node(validation.node_id, 'Reporting Station', kind='equipment',
        metadata={'equipment_id': 'REPORT-001', 'terminal_output': True})
    scenario = workflow.active_scenario
    scenario.name = 'One Staff Demo'
    scenario.workforce = [{'id': 'demo-staff', 'name': 'Alex'}]
    scenario.parameters.update(staff_capacity=1, interarrival_time=1 / 60,
                               system_variation=0, human_variation=0)
    return project


def configure_demo_analyzer_tests(project, test_codes):
    """Set the demo analyzer menu to every active LIS catalogue assay."""
    from app.system.model import WorkflowNode
    analyzer = next((node for node in project.workflow.graph.iter_nodes()
                     if node.metadata.get('equipment_id') == 'CHEM-001'), None)
    if analyzer is None:
        return False
    analyzer.widget.metadata['analyzer_tests'] = sorted({str(code) for code in test_codes})
    project.mark_dirty()
    return True
