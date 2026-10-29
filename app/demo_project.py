"""A small, self-contained laboratory for trying the simulation."""
from app.project import LabTycoonProject, ProjectEquipmentInstance


def create_demo_project(name="Demo Laboratory"):
    project = LabTycoonProject.create(
        name, 'One staff member receives, analyzes, reports, and validates demo orders.')
    project.floor.equipment_instances = [
        ProjectEquipmentInstance('RECP-001', 'specimens_reception_station', 3, 2),
        ProjectEquipmentInstance('CHEM-001', 'chemistry_analyzer', 3, 5),
        ProjectEquipmentInstance('REPORT-001', 'reporting_station', 6, 5),
    ]
    workflow = project.workflow
    source = workflow.create_node('Demo Orders', kind='order_adapter',
        metadata={'node_type': 'order adapter', 'adapter_type': 'simulation'})
    reception = workflow.create_connected_node(source.node_id, 'Specimen Registration',
        metadata={'node_type': 'specimen registration', 'required_staff': 1,
                  'human_throughput': 60, 'attend_equipment_id': 'RECP-001'})
    analyzer = workflow.create_connected_node(reception.node_id, 'Chemistry Analyzer', 20,
        kind='equipment', metadata={'node_type': 'automated analyzer',
            'equipment_id': 'CHEM-001', 'equipment_type': 'chemistry_analyzer',
            'analyzer_tests': ['TG'], 'throughput': 20,
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
