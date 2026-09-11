"""Tests for structured governance export helpers."""

from __future__ import annotations

from pathlib import Path
import csv
import json
import tempfile
import unittest

from app.system.governance import (
    GovernanceConsultation,
    export_governance_consultations_to_csv,
    export_governance_consultations_to_json,
    export_simulation_result_to_json,
    governance_consultations_to_dicts,
)


class GovernanceExportTests(unittest.TestCase):
    def test_governance_consultation_serialization_includes_standard_fields(self) -> None:
        consultation = GovernanceConsultation(
            sim_time=12.5,
            event_type="DUPLICATE_ORDER_CHECK",
            node_id="registration_1",
            order_id="ORD-2",
            specimen_id="specimen-2",
            provider_name="sop-as-code",
            decision_action="REQUEST_REVIEW",
            requires_human=True,
            reason="Order payload contains a duplicate-order indicator.",
            metadata={"rule_name": "possible_duplicate_order"},
        )

        payload = consultation.to_dict()

        self.assertEqual(payload["provider"], "sop-as-code")
        self.assertEqual(payload["provider_name"], "sop-as-code")
        self.assertEqual(payload["event_type"], "DUPLICATE_ORDER_CHECK")
        self.assertEqual(payload["metadata"]["rule_name"], "possible_duplicate_order")

    def test_export_governance_consultations_to_json(self) -> None:
        consultation = GovernanceConsultation(
            sim_time=1.0,
            event_type="ORDER_RECEIVED",
            node_id="registration_1",
            order_id="ORD-1",
            specimen_id="specimen-1",
            provider_name="sop-as-code",
            decision_action="ALLOW_ORDER",
            requires_human=False,
            reason="No duplicate-order indicators were present.",
            metadata={"rule_name": "possible_duplicate_order"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "governance.json"
            written = export_governance_consultations_to_json([consultation], output_path)

            self.assertEqual(written, output_path)
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["provider"], "sop-as-code")
        self.assertEqual(payload[0]["decision_action"], "ALLOW_ORDER")
        self.assertEqual(payload[0]["metadata"]["rule_name"], "possible_duplicate_order")

    def test_export_governance_consultations_to_csv(self) -> None:
        consultations = governance_consultations_to_dicts(
            [
                GovernanceConsultation(
                    sim_time=1.0,
                    event_type="ORDER_RECEIVED",
                    node_id="registration_1",
                    order_id="ORD-1",
                    specimen_id="specimen-1",
                    provider_name="sop-as-code",
                    decision_action="ALLOW_ORDER",
                    requires_human=False,
                    reason="No duplicate-order indicators were present.",
                    metadata={"rule_name": "possible_duplicate_order"},
                )
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "governance.csv"
            written = export_governance_consultations_to_csv(consultations, output_path)

            self.assertEqual(written, output_path)
            with output_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"], "sop-as-code")
        self.assertEqual(rows[0]["decision_action"], "ALLOW_ORDER")
        self.assertEqual(json.loads(rows[0]["metadata"])["rule_name"], "possible_duplicate_order")

    def test_export_simulation_result_to_json_uses_to_dict(self) -> None:
        class FakeResult:
            def to_dict(self) -> dict[str, object]:
                return {
                    "workflow_name": "Governance Workflow",
                    "governance_consultations": [
                        {
                            "sim_time": 1.0,
                            "event_type": "ORDER_RECEIVED",
                            "node_id": "registration_1",
                            "order_id": "ORD-1",
                            "specimen_id": "specimen-1",
                            "provider": "sop-as-code",
                            "decision_action": "ALLOW_ORDER",
                            "requires_human": False,
                            "reason": "No duplicate-order indicators were present.",
                            "metadata": {"rule_name": "possible_duplicate_order"},
                        }
                    ],
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "result.json"
            written = export_simulation_result_to_json(FakeResult(), output_path)
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(written, output_path)
        self.assertEqual(payload["workflow_name"], "Governance Workflow")
        self.assertEqual(payload["governance_consultations"][0]["provider"], "sop-as-code")


if __name__ == "__main__":
    unittest.main()
