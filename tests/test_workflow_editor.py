"""Tests for the graph-backed workflow editor model."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.system.model import BaseWidget, Workflow
from app.project import ProjectEquipmentInstance
from app.system.storage import WorkflowDocument, WorkflowRepository
from app.system.workflow_editor import (
    WorkflowEditorController,
    WorkflowGraphRenderer,
    WorkflowLayoutEngine,
    _center_scroll_fraction,
    _scroll_canvas_for_column,
    _normalize_node_type_label,
)


class WorkflowGraphModelTests(unittest.TestCase):
    def test_patient_resource_is_an_unconnected_persistent_declaration(self) -> None:
        workflow = Workflow()
        controller = WorkflowEditorController(on_change=lambda: None)
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)

        resource_id = controller.add_resource_node("patient", "generate_per_run")
        controller.update_resource(resource_id, "patient", "generate_per_run", True)
        self.assertEqual(workflow.get_node(resource_id).kind, "resource")
        self.assertEqual(workflow.get_node(resource_id).metadata["adapter_type"], "generate_per_run")
        with self.assertRaisesRegex(ValueError, "already has"):
            controller.add_resource_node("patient", "generate_per_run")
        with self.assertRaisesRegex(ValueError, "Unsupported Resource"):
            controller.add_resource_node("doctor", "generate_per_run")

        restored = Workflow.from_dict(workflow.to_dict()).get_node(resource_id)
        self.assertEqual(restored.metadata["resource_type"], "patient")
        self.assertEqual(restored.metadata["adapter_type"], "generate_per_run")

    def test_patient_resource_connects_to_order_adapter_but_not_processing_node(self) -> None:
        workflow = Workflow()
        controller = WorkflowEditorController(on_change=lambda: None)
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
        resource_id = controller.add_resource_node("patient", "generate_per_run")
        adapter_id = controller.add_order_adapter_node()
        task_id = workflow.create_node("Registration").node_id
        controller.selected_node_id = resource_id

        self.assertEqual([item[0] for item in controller.connection_target_choices()], [adapter_id])
        controller.connect_selected_node_to(adapter_id)
        self.assertEqual([(edge.source, edge.target) for edge in workflow.graph.edges], [(resource_id, adapter_id)])
        self.assertEqual(controller.connection_target_choices(), [])
        with self.assertRaisesRegex(ValueError, "only connect"):
            controller.connect_selected_node_to(task_id)
        controller.update_order_adapter_from_form(
            adapter_id, node_type="order adapter", name="Orders", adapter_type="simulation",
            description="", enabled=True,
        )

    def test_order_adapter_node_creation_and_persistence(self) -> None:
        workflow = Workflow()
        dirty_events: list[bool] = []
        controller = WorkflowEditorController(on_change=lambda: dirty_events.append(True))
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)

        node_id = controller.add_order_adapter_node()
        controller.update_order_adapter_from_form(
            node_id, node_type="order adapter", name="Order Intake",
            adapter_type="simulation", description="Synthetic arrivals", enabled=True,
        )
        node = workflow.get_node(node_id)
        self.assertEqual(node.kind, "order_adapter")
        self.assertEqual(node.widget.node_type, "order adapter")
        self.assertEqual(node.metadata["adapter_type"], "simulation")
        self.assertEqual(node.widget.throughput, 0.0)
        self.assertEqual(len(dirty_events), 2)

        restored = Workflow.from_dict(workflow.to_dict()).get_node(node_id)
        self.assertEqual(restored.kind, "order_adapter")
        self.assertEqual(restored.widget.name, "Order Intake")
        self.assertEqual(restored.metadata["adapter_type"], "simulation")

    def test_order_adapter_rejects_incoming_editor_connections(self) -> None:
        workflow = Workflow()
        source = workflow.create_node("Source", 1.0)
        controller = WorkflowEditorController(on_change=lambda: None)
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
        adapter_id = controller.add_order_adapter_node()
        controller.selected_node_id = source.node_id

        self.assertNotIn(adapter_id, {node_id for node_id, _ in controller.connection_target_choices()})
        with self.assertRaisesRegex(ValueError, "cannot have incoming"):
            controller.connect_selected_node_to(adapter_id)

    def test_equipment_nodes_reference_and_resolve_project_equipment(self) -> None:
        equipment = [ProjectEquipmentInstance("CHEM-001", "chemistry_analyzer", 4, 5)]
        workflow = Workflow()
        controller = WorkflowEditorController(equipment_instances=equipment, on_change=lambda: None)
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)

        node_id = controller.add_equipment_node("CHEM-001")
        node = workflow.get_node(node_id)

        self.assertEqual(node.kind, "equipment")
        self.assertEqual(node.metadata["equipment_id"], "CHEM-001")
        resolution = controller.resolve_equipment_node(node)
        self.assertTrue(resolution.resolved)
        self.assertEqual(resolution.display_name, "Chemistry Analyzer")

    def test_equipment_reference_survives_location_change_and_becomes_unresolved_on_delete(self) -> None:
        equipment = [ProjectEquipmentInstance("CENT-001", "centrifuge", 2, 2)]
        workflow = Workflow()
        controller = WorkflowEditorController(equipment_instances=equipment, on_change=lambda: None)
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
        node = workflow.get_node(controller.add_equipment_node("CENT-001"))

        equipment[0] = ProjectEquipmentInstance("CENT-001", "centrifuge", 7, 7, orientation=180)
        self.assertTrue(controller.resolve_equipment_node(node).resolved)
        self.assertEqual(node.metadata["equipment_id"], "CENT-001")

        equipment.clear()
        resolution = controller.resolve_equipment_node(node)
        self.assertFalse(resolution.resolved)
        self.assertEqual(resolution.display_name, "Missing Equipment")
        self.assertEqual(resolution.equipment_id, "CENT-001")

    def test_multiple_nodes_may_reference_the_same_equipment(self) -> None:
        equipment = [ProjectEquipmentInstance("CHEM-001", "chemistry_analyzer", 4, 5)]
        workflow = Workflow()
        controller = WorkflowEditorController(equipment_instances=equipment, on_change=lambda: None)
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)

        first_id = controller.add_equipment_node("CHEM-001")
        second_id = controller.add_equipment_node("CHEM-001")

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(workflow.get_node(first_id).metadata["equipment_id"], "CHEM-001")
        self.assertEqual(workflow.get_node(second_id).metadata["equipment_id"], "CHEM-001")

    def test_equipment_nodes_can_connect_to_each_other(self) -> None:
        equipment = [
            ProjectEquipmentInstance("CENT-001", "centrifuge", 2, 2),
            ProjectEquipmentInstance("CHEM-001", "chemistry_analyzer", 4, 5),
        ]
        dirty_events: list[bool] = []
        workflow = Workflow()
        controller = WorkflowEditorController(
            equipment_instances=equipment,
            on_change=lambda: dirty_events.append(True),
        )
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
        source_id = controller.add_equipment_node("CENT-001")
        target_id = controller.add_equipment_node("CHEM-001")
        controller.selected_node_id = source_id

        choices = controller.connection_target_choices()
        self.assertTrue(any(node_id == target_id and "CHEM-001" in label for node_id, label in choices))

        controller.connect_selected_node_to(target_id)

        self.assertEqual(
            [(edge.source, edge.target) for edge in workflow.graph.edges],
            [(source_id, target_id)],
        )
        self.assertEqual(len(dirty_events), 3)

    def test_preprocessing_can_connect_to_two_equipment_nodes_without_losing_branches(self) -> None:
        equipment = [
            ProjectEquipmentInstance("CENT-001", "centrifuge", 2, 2),
            ProjectEquipmentInstance("CENT-002", "centrifuge", 5, 5),
        ]
        dirty_events: list[bool] = []
        workflow = Workflow()
        controller = WorkflowEditorController(equipment_instances=equipment, on_change=lambda: dirty_events.append(True))
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
        source = workflow.create_node("Preprocessing", 1.0)
        first = controller.add_equipment_node("CENT-001")
        second = controller.add_equipment_node("CENT-002")
        controller.selected_node_id = source.node_id

        choices = controller.connection_target_choices()
        self.assertEqual({node_id for node_id, _ in choices}, {first, second})
        controller.connect_selected_node_to_many([first, second])
        self.assertEqual({edge.target for edge in workflow.graph.outgoing_edges(source.node_id)}, {first, second})

        controller.update_node_from_form(
            source.node_id, node_type="specimens processing", name="Preprocessing", description="",
            throughput=1.0, human_throughput=0.0, capacity=1, required_staff=0,
            outgoing_connection_node_id=first,
        )
        self.assertEqual({edge.target for edge in workflow.graph.outgoing_edges(source.node_id)}, {first, second})
        self.assertEqual(len(dirty_events), 4)

    def test_binding_and_rebinding_equipment_marks_project_dirty(self) -> None:
        equipment = [
            ProjectEquipmentInstance("CENT-001", "centrifuge", 2, 2),
            ProjectEquipmentInstance("CHEM-001", "chemistry_analyzer", 4, 5),
        ]
        dirty_events: list[bool] = []
        workflow = Workflow()
        controller = WorkflowEditorController(
            equipment_instances=equipment,
            on_change=lambda: dirty_events.append(True),
        )
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
        controller.add_equipment_node("CENT-001")

        controller.bind_selected_equipment("CHEM-001")

        node = controller.selected_node()
        self.assertEqual(node.metadata["equipment_id"], "CHEM-001")
        self.assertEqual(len(dirty_events), 2)

    def test_equipment_operating_settings_persist_without_copying_floor_properties(self) -> None:
        equipment = [ProjectEquipmentInstance("CHEM-001", "chemistry_analyzer", 4, 5)]
        dirty_events: list[bool] = []
        workflow = Workflow()
        controller = WorkflowEditorController(equipment_instances=equipment, on_change=lambda: dirty_events.append(True))
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
        node_id = controller.add_equipment_node(
            "CHEM-001", throughput=60.0, human_throughput=15.0,
            capacity=2, required_staff=1, description="Primary analyzer", enabled=False,
        )
        node = workflow.get_node(node_id)
        self.assertEqual(node.widget.throughput, 60.0)
        self.assertEqual(node.widget.service_time, 60.0)
        self.assertEqual(node.widget.human_throughput, 15.0)
        self.assertEqual(node.widget.capacity, 2)
        self.assertEqual(node.widget.required_staff, 1)
        self.assertFalse(node.widget.enabled)
        self.assertEqual(node.widget.description, "Primary analyzer")
        self.assertNotIn("row", node.metadata)
        self.assertNotIn("orientation", node.metadata)

        restored = Workflow.from_dict(workflow.to_dict()).get_node(node_id)
        self.assertEqual(restored.metadata["equipment_id"], "CHEM-001")
        self.assertEqual(restored.widget.throughput, 60.0)
        self.assertEqual(restored.widget.required_staff, 1)
        self.assertFalse(restored.widget.enabled)
        self.assertEqual(len(dirty_events), 1)

    def test_editing_missing_equipment_keeps_reference_and_settings(self) -> None:
        equipment = [ProjectEquipmentInstance("CENT-001", "centrifuge", 2, 2)]
        workflow = Workflow()
        controller = WorkflowEditorController(equipment_instances=equipment, on_change=lambda: None)
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
        node_id = controller.add_equipment_node("CENT-001")
        equipment.clear()

        controller.bind_selected_equipment("CENT-001", throughput=30.0, capacity=2)

        node = workflow.get_node(node_id)
        self.assertFalse(controller.resolve_equipment_node(node).resolved)
        self.assertEqual(node.metadata["equipment_id"], "CENT-001")
        self.assertEqual(node.widget.throughput, 30.0)
        self.assertEqual(node.widget.capacity, 2)

    def test_workflow_roundtrip_preserves_nodes_edges_and_metadata(self) -> None:
        workflow = Workflow()
        intake = workflow.create_node("Intake", 1.0)
        review = workflow.create_branch_node(intake.node_id, "Review", metadata={"description": "Branch point"})
        analyzer = workflow.create_connected_node(review.node_id, "Analyzer", 2.5)
        workflow.update_node_properties(
            analyzer.node_id,
            metadata={"description": "Runs the main assay"},
            kind="task",
        )

        payload = workflow.to_dict()
        restored = Workflow.from_dict(payload)

        self.assertEqual(len(restored.graph.nodes), 3)
        self.assertEqual(len(restored.graph.edges), 2)
        self.assertEqual(restored.get_node(intake.node_id).widget.name, "Intake")
        self.assertEqual(restored.get_node(review.node_id).kind, "branch")
        self.assertEqual(
            restored.get_node(review.node_id).widget.metadata["description"],
            "Branch point",
        )
        self.assertEqual(
            restored.get_node(analyzer.node_id).widget.metadata["description"],
            "Runs the main assay",
        )

    def test_delete_node_removes_attached_edges(self) -> None:
        workflow = Workflow()
        source = workflow.create_node("Source", 1.0)
        target = workflow.create_connected_node(source.node_id, "Target", 2.0)

        workflow.delete_node(target.node_id)

        self.assertEqual(len(workflow.graph.nodes), 1)
        self.assertEqual(len(workflow.graph.edges), 0)
        self.assertEqual(source.widget.downstream_widgets, [])

    def test_reassign_connection_replaces_existing_edge(self) -> None:
        workflow = Workflow()
        source = workflow.create_node("Source", 1.0)
        initial_target = workflow.create_connected_node(source.node_id, "Initial", 2.0)
        replacement_target = workflow.create_node("Replacement", 3.0)

        workflow.graph.reassign_connection(source.node_id, replacement_target.node_id)

        outgoing_targets = [edge.target for edge in workflow.graph.outgoing_edges(source.node_id)]
        self.assertEqual(outgoing_targets, [replacement_target.node_id])
        self.assertEqual(source.widget.downstream_widgets, [replacement_target.widget])
        self.assertEqual(initial_target.widget.downstream_widgets, [])

    def test_legacy_ai_agent_node_type_normalizes_to_evaluation(self) -> None:
        self.assertEqual(_normalize_node_type_label("AI agent"), "Evaluation")
        self.assertEqual(_normalize_node_type_label("Evaluation"), "Evaluation")


class WorkflowLayoutTests(unittest.TestCase):
    def test_order_adapter_renders_as_source_node(self) -> None:
        class RecordingCanvas:
            def __init__(self) -> None:
                self.rectangles = 0
                self.labels: list[str] = []

            def draw_rectangle(self, *args: object, **kwargs: object) -> None:
                self.rectangles += 1

            def draw_text(self, label: str, *args: object, **kwargs: object) -> None:
                self.labels.append(label)

        workflow = Workflow()
        node = workflow.create_node(
            "Order Intake", kind="order_adapter",
            metadata={"node_type": "order adapter", "adapter_type": "simulation"},
        )
        canvas = RecordingCanvas()

        WorkflowGraphRenderer()._draw_node(canvas, node, (300, 300), selected=False)

        self.assertEqual(canvas.rectangles, 1)
        self.assertIn("Order Intake", canvas.labels)
        self.assertIn("Source: simulation", canvas.labels)

    def test_scrollable_column_resolves_its_inner_canvas(self) -> None:
        canvas = object()
        wrapped_column = SimpleNamespace(Widget=SimpleNamespace(canvas=canvas))
        frame_column = SimpleNamespace(TKColFrame=SimpleNamespace(canvas=canvas), Widget=object())
        direct_column = SimpleNamespace(Widget=canvas)

        self.assertIs(_scroll_canvas_for_column(wrapped_column), canvas)
        self.assertIs(_scroll_canvas_for_column(frame_column), canvas)
        self.assertIs(_scroll_canvas_for_column(direct_column), canvas)

    def test_scroll_fraction_centers_and_clamps_node_position(self) -> None:
        self.assertAlmostEqual(_center_scroll_fraction(1200, 800, 0, 2400), 1 / 3)
        self.assertEqual(_center_scroll_fraction(100, 800, 0, 2400), 0.0)
        self.assertAlmostEqual(_center_scroll_fraction(2300, 800, 0, 2400), 2 / 3)
        self.assertEqual(_center_scroll_fraction(500, 800, 0, 600), 0.0)

    def test_node_table_row_maps_to_graph_node(self) -> None:
        workflow = Workflow()
        first = workflow.create_node("First", 1.0)
        second = workflow.create_node("Second", 1.0)
        controller = WorkflowEditorController()
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)

        self.assertEqual(controller.node_id_for_table_row(0), first.node_id)
        self.assertEqual(controller.node_id_for_table_row(1), second.node_id)
        self.assertIsNone(controller.node_id_for_table_row(2))

    def test_layout_places_children_below_parents(self) -> None:
        workflow = Workflow()
        root = workflow.create_node("Root", 1.0)
        left = workflow.create_connected_node(root.node_id, "Left", 1.0)
        right = workflow.create_connected_node(root.node_id, "Right", 1.0)
        leaf = workflow.create_connected_node(left.node_id, "Leaf", 1.0)

        layout = WorkflowLayoutEngine(canvas_width=1200, canvas_height=900)
        positions = layout.layout(workflow)

        self.assertGreater(positions[root.node_id][1], positions[left.node_id][1])
        self.assertGreater(positions[root.node_id][1], positions[right.node_id][1])
        self.assertGreater(positions[left.node_id][1], positions[leaf.node_id][1])

    def test_layout_is_available_for_empty_workflows(self) -> None:
        workflow = Workflow()
        layout = WorkflowLayoutEngine()

        self.assertEqual(layout.layout(workflow), {})


class WorkflowCompatibilityTests(unittest.TestCase):
    def test_base_widget_serialization_preserves_metadata(self) -> None:
        widget = BaseWidget("Widget", 3.5, metadata={"description": "note"})

        payload = widget.to_dict()
        restored = BaseWidget.from_dict(payload)

        self.assertEqual(restored.name, "Widget")
        self.assertEqual(restored.service_time, 3.5)
        self.assertEqual(restored.metadata["description"], "note")

    def test_base_widget_serialization_preserves_node_configuration(self) -> None:
        widget = BaseWidget(
            "Node 1",
            8.0,
            metadata={
                "node_type": "manual analyzer",
                "throughput": 8.0,
                "human_throughput": 1.5,
                "capacity": 3,
                "enabled": False,
                "description": "Manual review",
                "required_staff": 2,
            },
        )

        payload = widget.to_dict()
        restored = BaseWidget.from_dict(payload)

        self.assertEqual(restored.node_type, "manual analyzer")
        self.assertEqual(restored.throughput, 8.0)
        self.assertEqual(restored.human_throughput, 1.5)
        self.assertEqual(restored.capacity, 3)
        self.assertFalse(restored.enabled)
        self.assertEqual(restored.description, "Manual review")
        self.assertEqual(restored.required_staff, 2)

    def test_base_widget_defaults_capacity_to_one(self) -> None:
        widget = BaseWidget("Widget", 3.5, metadata={"description": "note"})

        self.assertEqual(widget.capacity, 1)
        self.assertTrue(widget.enabled)

    def test_disconnect_selected_node_removes_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repository = WorkflowRepository(tmpdir)
            workflow = Workflow()
            source = workflow.create_node("Source", 1.0)
            target = workflow.create_connected_node(source.node_id, "Target", 2.0)

            controller = WorkflowEditorController(repository=repository)
            controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
            controller.selected_node_id = source.node_id

            controller.disconnect_selected_node_from(target.node_id)

            outgoing_targets = [edge.target for edge in workflow.graph.outgoing_edges(source.node_id)]
            self.assertEqual(outgoing_targets, [])
            self.assertEqual(source.widget.downstream_widgets, [])

class WorkflowPersistenceTests(unittest.TestCase):
    def test_new_workflow_is_persisted_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            controller = WorkflowEditorController(repository=WorkflowRepository(tmpdir))

            controller.create_new_workflow()

            assert controller.current_document is not None
            saved_path = Path(tmpdir) / f"{controller.current_document.workflow_id}.json"
            self.assertTrue(saved_path.exists())

    def test_editor_changes_are_saved_without_manual_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repository = WorkflowRepository(tmpdir)
            controller = WorkflowEditorController(repository=repository)

            controller.create_new_workflow()
            assert controller.current_document is not None

            node_id = controller.add_node()
            controller.add_scenario()

            loaded = repository.load(controller.current_document.workflow_id)
            self.assertEqual(len(loaded.workflow.graph.nodes), 1)
            self.assertEqual(len(loaded.workflow.scenarios), 2)
            self.assertIsNotNone(loaded.workflow.get_node(node_id))

    def test_add_node_keeps_existing_selection_and_refreshes_connect_menu(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repository = WorkflowRepository(tmpdir)
            workflow = Workflow()
            source = workflow.create_node("Source", 1.0)
            controller = WorkflowEditorController(repository=repository)
            controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
            controller.selected_node_id = source.node_id

            new_node_id = controller.add_node()

            self.assertEqual(controller.selected_node_id, source.node_id)
            self.assertIsNotNone(controller.current_document.workflow.get_node(new_node_id))
            choices = controller.connection_target_choices()
            self.assertTrue(any(node_id == new_node_id for node_id, _ in choices))

    def test_update_node_from_form_can_add_outgoing_and_incoming_connections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repository = WorkflowRepository(tmpdir)
            workflow = Workflow()
            source = workflow.create_node("Source", 1.0)
            middle = workflow.create_node("Middle", 1.0)
            target = workflow.create_node("Target", 1.0)
            controller = WorkflowEditorController(repository=repository)
            controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)

            controller.update_node_from_form(
                middle.node_id,
                node_type="specimens processing",
                name="Middle",
                description="Updated",
                throughput=2.0,
                human_throughput=1.0,
                capacity=1,
                required_staff=0,
                incoming_connection_node_id=source.node_id,
                outgoing_connection_node_id=target.node_id,
            )

            outgoing_targets = [edge.target for edge in workflow.graph.outgoing_edges(middle.node_id)]
            incoming_sources = [edge.source for edge in workflow.graph.incoming_edges(middle.node_id)]
            self.assertIn(target.node_id, outgoing_targets)
            self.assertIn(source.node_id, incoming_sources)

    def test_update_node_from_form_updates_enabled_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow = Workflow()
            node = workflow.create_node("Node", 1.0)

            controller = WorkflowEditorController(repository=WorkflowRepository(tmpdir))
            controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)

            controller.update_node_from_form(
                node.node_id,
                node_type="specimens processing",
                name="Node",
                description="Updated",
                throughput=1.0,
                human_throughput=0.0,
                capacity=1,
                required_staff=0,
                enabled=False,
            )

            updated = workflow.get_node(node.node_id)
            assert updated is not None
            self.assertFalse(updated.widget.enabled)

    def test_analyzer_test_selection_is_persisted_in_node_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repository = WorkflowRepository(tmpdir)
            controller = WorkflowEditorController(repository=repository)
            controller.create_new_workflow()
            assert controller.current_document is not None

            node = controller.current_document.workflow.create_node(
                "Analyzer",
                1.0,
                metadata={"node_type": "automated analyzer"},
            )
            controller.selected_node_id = node.node_id

            controller.set_analyzer_tests(node.node_id, ["CBC", "GLU"])

            reloaded = repository.load(controller.current_document.workflow_id)
            stored_node = reloaded.workflow.get_node(node.node_id)
            assert stored_node is not None
            self.assertEqual(stored_node.widget.metadata["analyzer_tests"], ["CBC", "GLU"])

    def test_editing_node_type_does_not_change_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow = Workflow()
            root = workflow.create_node("Root", 1.0)
            branch = workflow.create_branch_node(root.node_id, "Branch")

            controller = WorkflowEditorController(repository=WorkflowRepository(tmpdir))
            controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)

            controller.update_node_from_form(
                branch.node_id,
                node_type="manual analyzer",
                name="Branch",
                description="Updated",
                throughput=2.0,
                human_throughput=1.5,
                capacity=2,
                required_staff=1,
            )

            updated = workflow.get_node(branch.node_id)
            assert updated is not None
            self.assertEqual(updated.kind, "branch")
            self.assertEqual(updated.widget.node_type, "manual analyzer")


if __name__ == "__main__":
    unittest.main()
