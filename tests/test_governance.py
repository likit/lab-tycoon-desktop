"""Tests for the workflow governance layer."""

from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from app.system.governance import (
    ACTION_ALLOW_ORDER,
    ACTION_CONTINUE,
    ACTION_REJECT_DUPLICATE,
    EVENT_DUPLICATE_ORDER_DETECTED,
    ACTION_REQUEST_REVIEW,
    Decision,
    EVENT_DUPLICATE_ORDER_CHECK,
    EVENT_ORDER_RECEIVED,
    GovernanceEvent,
    GovernanceProvider,
    SopAsCodeGovernanceProvider,
)
from app.system.model import Workflow
from app.system.order_sources import InMemoryCustomer, InMemoryOrder, InMemoryOrderItem, InMemoryTest
from app.system.simulation import run_workflow_simulation
from app.system.storage import WorkflowDocument


def _make_order(
    *,
    order_id: str,
    customer_id: str,
    ordered_at: datetime,
    test_code: str = "CBC",
) -> InMemoryOrder:
    customer = InMemoryCustomer(id=customer_id, hn=customer_id, fullname=f"Patient {customer_id}")
    return InMemoryOrder(
        id=order_id,
        customer_id=customer_id,
        order_datetime=ordered_at,
        order_items=[InMemoryOrderItem(InMemoryTest(test_code))],
        priority="routine",
        customer=customer,
    )


class GovernanceProviderTests(unittest.TestCase):
    def test_sop_as_code_provider_reuses_duplicate_order_rule_context(self) -> None:
        provider = SopAsCodeGovernanceProvider()
        current = _make_order(
            order_id="ORD-2",
            customer_id="P001",
            ordered_at=datetime(2026, 5, 2, 11, 0, 0),
        )
        prior = _make_order(
            order_id="ORD-1",
            customer_id="P001",
            ordered_at=datetime(2026, 5, 2, 10, 0, 0),
        )

        decision = provider.evaluate(
            GovernanceEvent(
                event_type=EVENT_DUPLICATE_ORDER_CHECK,
                node_id="specimen_registration_1",
                order_id=current.id,
                specimen_id="specimen-2",
                sim_time=12.5,
                context={"order": current, "prior_orders": [prior]},
            )
        )

        self.assertEqual(decision.action, ACTION_REQUEST_REVIEW)
        self.assertTrue(decision.requires_human)
        self.assertEqual(decision.reason, "Order payload contains a duplicate-order indicator.")
        self.assertTrue(decision.metadata["rule_result"]["matched"])
        self.assertCountEqual(
            decision.metadata["rule_result"]["metadata"]["matched_fields"],
            [
                "payload.duplicate_candidate",
                "payload.repeated_indicator",
                "payload.possible_duplicate",
            ],
        )

    def test_sop_as_code_provider_allows_non_duplicate_orders(self) -> None:
        provider = SopAsCodeGovernanceProvider()
        current = _make_order(
            order_id="ORD-3",
            customer_id="P002",
            ordered_at=datetime(2026, 5, 2, 11, 0, 0),
        )

        decision = provider.evaluate(
            GovernanceEvent(
                event_type=EVENT_ORDER_RECEIVED,
                node_id="specimen_registration_1",
                order_id=current.id,
                specimen_id="specimen-3",
                sim_time=0.0,
                context={"order": current, "prior_orders": []},
            )
        )

        self.assertEqual(decision.action, ACTION_ALLOW_ORDER)
        self.assertFalse(decision.requires_human)
        self.assertFalse(decision.metadata["rule_result"]["matched"])

    def test_workflow_nodes_preserve_governance_event_metadata(self) -> None:
        workflow = Workflow()
        node = workflow.create_node(
            "Registration",
            1.0,
            metadata={
                "governance_events": [
                    "ORDER_RECEIVED",
                    {"event_type": "DUPLICATE_ORDER_CHECK", "matched_action": "REQUEST_REVIEW"},
                ]
            },
        )

        restored = Workflow.from_dict(workflow.to_dict())
        restored_node = restored.get_node(node.node_id)

        self.assertIsNotNone(restored_node)
        self.assertEqual(
            restored_node.metadata["governance_events"],
            [
                "ORDER_RECEIVED",
                {"event_type": "DUPLICATE_ORDER_CHECK", "matched_action": "REQUEST_REVIEW"},
            ],
        )


class GovernanceSimulationTests(unittest.TestCase):
    class StubGovernanceProvider(GovernanceProvider):
        provider_name = "test-governance"

        def __init__(self, decision: Decision) -> None:
            self.decision = decision

        def evaluate(self, event: GovernanceEvent) -> Decision:
            return self.decision

    def _evaluation_workflow(self) -> Workflow:
        workflow = Workflow()
        evaluation = workflow.create_node(
            "Evaluation",
            0.0,
            metadata={
                "node_type": "Evaluation",
                "plugin": "duplicate_order_detector",
            },
        )
        analyzer = workflow.create_connected_node(evaluation.node_id, "Analyzer", 1.0,
            metadata={"node_type": "automated analyzer", "analyzer_tests": ["CBC"]})
        workflow.create_connected_node(analyzer.node_id, "Release", 1.0)
        return workflow

    def test_registration_node_logs_governance_consultations_and_continues(self) -> None:
        workflow = Workflow()
        registration = workflow.create_node(
            "Registration",
            1.0,
            metadata={
                "node_type": "specimen registration",
            },
        )
        analyzer = workflow.create_connected_node(registration.node_id, "Analyzer", 1.0,
            metadata={"node_type": "automated analyzer", "analyzer_tests": ["CBC"]})
        workflow.create_connected_node(analyzer.node_id, "Release", 1.0)

        first_order = _make_order(
            order_id="ORD-1",
            customer_id="P001",
            ordered_at=datetime(2026, 5, 2, 10, 0, 0),
        )
        second_order = _make_order(
            order_id="ORD-2",
            customer_id="P001",
            ordered_at=datetime(2026, 5, 2, 11, 0, 0),
        )

        result = run_workflow_simulation(
            specimens_number=2,
            workflow_document=WorkflowDocument.create("Governance Workflow", workflow=workflow),
            orders=[first_order, second_order],
            random_seed=7,
        )

        self.assertEqual(len(result.completed_specimens), 2)
        self.assertGreaterEqual(len(result.governance_consultations), 4)
        self.assertTrue(
            any(
                consultation.event_type == EVENT_DUPLICATE_ORDER_CHECK
                and consultation.order_id == "ORD-2"
                and consultation.decision_action == ACTION_REQUEST_REVIEW
                for consultation in result.governance_consultations
            )
        )
        self.assertIn(
            "governance_consultations",
            result.completed_specimens[0].metadata,
        )
        self.assertTrue(result.to_dict()["governance_consultations"])

    def test_evaluation_node_calls_detect_duplicate_candidate(self) -> None:
        workflow = self._evaluation_workflow()
        order = _make_order(
            order_id="ORD-10",
            customer_id="P010",
            ordered_at=datetime(2026, 5, 2, 10, 0, 0),
        )

        with patch("app.system.simulation.lab.detect_duplicate_candidate", return_value=False) as detect_mock:
            run_workflow_simulation(
                specimens_number=1,
                workflow_document=WorkflowDocument.create("Evaluation Workflow", workflow=workflow),
                orders=[order],
                random_seed=7,
            )

        detect_mock.assert_called_once()

    def test_evaluation_node_without_duplicate_candidate_creates_no_governance_consultation(self) -> None:
        workflow = self._evaluation_workflow()
        order = _make_order(
            order_id="ORD-11",
            customer_id="P011",
            ordered_at=datetime(2026, 5, 2, 10, 0, 0),
        )

        with patch("app.system.simulation.lab.detect_duplicate_candidate", return_value=False):
            result = run_workflow_simulation(
                specimens_number=1,
                workflow_document=WorkflowDocument.create("Evaluation Workflow", workflow=workflow),
                orders=[order],
                random_seed=7,
            )

        self.assertEqual(result.governance_consultations, [])

    def test_evaluation_node_with_duplicate_candidate_records_governance_consultation(self) -> None:
        workflow = self._evaluation_workflow()
        prior_order = _make_order(
            order_id="ORD-12",
            customer_id="P012",
            ordered_at=datetime(2026, 5, 2, 9, 0, 0),
        )
        current_order = _make_order(
            order_id="ORD-13",
            customer_id="P012",
            ordered_at=datetime(2026, 5, 2, 10, 0, 0),
        )

        with patch("app.system.simulation.lab.detect_duplicate_candidate", return_value=True):
            result = run_workflow_simulation(
                specimens_number=2,
                workflow_document=WorkflowDocument.create("Evaluation Workflow", workflow=workflow),
                orders=[prior_order, current_order],
                random_seed=7,
            )

        self.assertTrue(result.governance_consultations)
        self.assertTrue(
            any(
                consultation.event_type == EVENT_DUPLICATE_ORDER_DETECTED
                for consultation in result.governance_consultations
            )
        )
        self.assertTrue(
            any(
                consultation.decision_action == ACTION_REQUEST_REVIEW
                for consultation in result.governance_consultations
            )
        )

    def test_workflows_without_evaluation_node_remain_unchanged(self) -> None:
        workflow = Workflow()
        intake = workflow.create_node("Intake", 1.0)
        analyzer = workflow.create_connected_node(intake.node_id, "Analyzer", 1.0,
            metadata={"node_type": "automated analyzer", "analyzer_tests": ["CBC"]})
        workflow.create_connected_node(analyzer.node_id, "Release", 1.0)
        order = _make_order(
            order_id="ORD-14",
            customer_id="P014",
            ordered_at=datetime(2026, 5, 2, 10, 0, 0),
        )

        with patch("app.system.simulation.lab.detect_duplicate_candidate") as detect_mock:
            result = run_workflow_simulation(
                specimens_number=1,
                workflow_document=WorkflowDocument.create("Plain Workflow", workflow=workflow),
                orders=[order],
                random_seed=7,
            )

        detect_mock.assert_not_called()
        self.assertEqual(len(result.completed_specimens), 1)
        self.assertEqual(result.governance_consultations, [])

    def test_governance_target_node_overrides_normal_routing(self) -> None:
        workflow = Workflow()
        intake = workflow.create_node(
            "Intake",
            0.0,
            metadata={"governance_events": ["ROUTE_ORDER"]},
        )
        normal = workflow.create_connected_node(intake.node_id, "Normal", 1.0)
        target = workflow.create_connected_node(normal.node_id, "Target", 1.0)
        provider = self.StubGovernanceProvider(
            Decision(
                action=ACTION_CONTINUE,
                requires_human=False,
                reason="Route to the governed target.",
                target_node=target.node_id,
            )
        )

        result = run_workflow_simulation(
            specimens_number=1,
            workflow_document=WorkflowDocument.create("Governed Routing", workflow=workflow),
            governance_provider=provider,
            random_seed=7,
        )

        self.assertEqual(result.node_stats[normal.node_id].specimens_processed, 0)
        self.assertEqual(result.node_stats[target.node_id].specimens_processed, 1)
        self.assertEqual(result.governance_consultations[0].metadata["target_node"], target.node_id)

    def test_governance_rejection_stops_specimen_processing(self) -> None:
        workflow = Workflow()
        intake = workflow.create_node(
            "Intake",
            0.0,
            metadata={"governance_events": ["REVIEW_ORDER"]},
        )
        release = workflow.create_connected_node(intake.node_id, "Release", 1.0)
        provider = self.StubGovernanceProvider(
            Decision(
                action=ACTION_REJECT_DUPLICATE,
                requires_human=False,
                reason="Confirmed duplicate.",
                target_node=None,
            )
        )

        result = run_workflow_simulation(
            specimens_number=1,
            workflow_document=WorkflowDocument.create("Governed Rejection", workflow=workflow),
            governance_provider=provider,
            random_seed=7,
        )

        self.assertEqual(len(result.completed_specimens), 1)
        self.assertEqual(result.node_stats[release.node_id].specimens_processed, 0)
        self.assertTrue(
            any(event["event"] == "governance_rejected" for event in result.completed_specimens[0].history)
        )


if __name__ == "__main__":
    unittest.main()
