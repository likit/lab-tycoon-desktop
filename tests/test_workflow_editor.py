"""Tests for the graph-backed workflow editor model."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from app.system.model import BaseWidget, Workflow
from app.system.storage import WorkflowDocument, WorkflowRepository
from app.system.workflow_editor import (
    WorkflowEditorController,
    WorkflowLayoutEngine,
    _normalize_node_type_label,
    _selected_connection_choices,
    _workflow_node_choices,
)


class WorkflowGraphModelTests(unittest.TestCase):
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
            choices = _workflow_node_choices(workflow, exclude_node_id=source.node_id)
            self.assertIn(f"Node 2::{new_node_id}", choices)

    def test_connection_defaults_reflect_existing_edges(self) -> None:
        workflow = Workflow()
        source = workflow.create_node("Source", 1.0)
        existing = workflow.create_connected_node(source.node_id, "Existing", 2.0)
        controller = WorkflowEditorController()
        controller.current_document = WorkflowDocument.create("Workflow", workflow=workflow)
        controller.selected_node_id = source.node_id

        outgoing_defaults = _selected_connection_choices(workflow, node_id=source.node_id, direction="outgoing")
        incoming_defaults = _selected_connection_choices(workflow, node_id=source.node_id, direction="incoming")

        self.assertEqual(outgoing_defaults, [f"Existing::{existing.node_id}"])
        self.assertEqual(incoming_defaults, [])

    def test_modal_node_choices_are_stable_and_exclude_only_self(self) -> None:
        workflow = Workflow()
        first = workflow.create_node("Gamma", 1.0)
        alpha = workflow.create_node("Alpha", 1.0)
        beta = workflow.create_node("Beta", 1.0)

        choices = _workflow_node_choices(workflow, exclude_node_id=first.node_id)

        self.assertEqual(
            choices,
            [
                f"Alpha::{alpha.node_id}",
                f"Beta::{beta.node_id}",
            ],
        )

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
        workflow = Workflow()
        node = workflow.create_node("Node", 1.0)

        controller = WorkflowEditorController()
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
        workflow = Workflow()
        root = workflow.create_node("Root", 1.0)
        branch = workflow.create_branch_node(root.node_id, "Branch")

        controller = WorkflowEditorController()
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
