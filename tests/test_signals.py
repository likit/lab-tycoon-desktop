"""Tests for order-derived rule signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import unittest

from app.system.orchestration import RuleEngine
from app.system.orchestration.rule_preview_ui import (
    build_stewardship_ai_summary,
    build_order_preview_context_json,
    list_orders_flagged_by_current_rule,
)
from app.system.signals.context_builder import build_rule_context
from app.system.signals.duplicate import detect_duplicate_candidate


@dataclass
class FakeTest:
    code: str


@dataclass
class FakeOrderItem:
    test: FakeTest


@dataclass
class FakeOrder:
    id: int
    customer_id: int
    order_datetime: datetime
    order_items: list[FakeOrderItem]
    priority: str = "routine"


def make_order(
    order_id: int,
    customer_id: int = 1,
    ordered_at: datetime | None = None,
    test_codes: tuple[str, ...] = ("CBC",),
    priority: str = "routine",
) -> FakeOrder:
    return FakeOrder(
        id=order_id,
        customer_id=customer_id,
        order_datetime=ordered_at or datetime(2026, 4, 22, 10, 0),
        order_items=[FakeOrderItem(FakeTest(code)) for code in test_codes],
        priority=priority,
    )


class DuplicateSignalTests(unittest.TestCase):
    def test_detect_duplicate_candidate_for_same_patient_and_test_within_window(self) -> None:
        current = make_order(2)
        prior = make_order(1, ordered_at=current.order_datetime - timedelta(hours=1))

        self.assertTrue(detect_duplicate_candidate(current, [prior], window_hours=4))

    def test_detect_duplicate_candidate_false_without_relevant_prior_order(self) -> None:
        current = make_order(2)
        different_patient = make_order(
            1,
            customer_id=9,
            ordered_at=current.order_datetime - timedelta(hours=1),
        )
        different_test = make_order(
            3,
            ordered_at=current.order_datetime - timedelta(hours=1),
            test_codes=("HDL",),
        )

        self.assertFalse(
            detect_duplicate_candidate(current, [different_patient, different_test], window_hours=4)
        )

    def test_detect_duplicate_candidate_false_outside_window(self) -> None:
        current = make_order(2)
        prior = make_order(1, ordered_at=current.order_datetime - timedelta(hours=5))

        self.assertFalse(detect_duplicate_candidate(current, [prior], window_hours=4))

    def test_detect_duplicate_candidate_does_not_match_current_order_against_itself(self) -> None:
        current = make_order(2)

        self.assertFalse(detect_duplicate_candidate(current, [current], window_hours=4))

    def test_build_rule_context_includes_order_fields_and_duplicate_signal(self) -> None:
        current = make_order(2, priority="urgent")
        prior = make_order(1, ordered_at=current.order_datetime - timedelta(hours=1))

        context = build_rule_context(current, prior_orders=[prior], window_hours=4)
        payload = context["payload"]

        self.assertEqual(payload["order_id"], "2")
        self.assertEqual(payload["patient_id"], "1")
        self.assertEqual(payload["test_code"], "CBC")
        self.assertEqual(payload["priority"], "urgent")
        self.assertTrue(payload["duplicate_candidate"])

    def test_context_alias_fields_match_duplicate_candidate_for_now(self) -> None:
        current = make_order(2)
        prior = make_order(1, ordered_at=current.order_datetime - timedelta(hours=1))

        payload = build_rule_context(current, prior_orders=[prior])["payload"]

        self.assertEqual(payload["repeated_indicator"], payload["duplicate_candidate"])
        self.assertEqual(payload["possible_duplicate"], payload["duplicate_candidate"])

    def test_existing_rule_engine_consumes_generated_context(self) -> None:
        current = make_order(2)
        prior = make_order(1, ordered_at=current.order_datetime - timedelta(hours=1))
        context = build_rule_context(current, prior_orders=[prior])

        result = RuleEngine().evaluate("possible_duplicate_order", context)

        self.assertTrue(result.matched)

    def test_preview_helper_accepts_injected_order_context_builder(self) -> None:
        def fake_context_builder(order_id: int | str, window_hours: int = 4) -> dict:
            return {
                "payload": {
                    "order_id": str(order_id),
                    "patient_id": "1",
                    "test_code": "CBC",
                    "priority": "routine",
                    "duplicate_candidate": False,
                    "repeated_indicator": False,
                    "possible_duplicate": False,
                }
            }

        result = build_order_preview_context_json("42", context_builder=fake_context_builder)

        self.assertTrue(result["ok"])
        self.assertEqual(json.loads(result["context_json"]), result["context"])
        self.assertEqual(result["context"]["payload"]["order_id"], "42")

    def test_current_rule_lists_only_flagged_order_contexts(self) -> None:
        def fake_context_row_builder(window_hours: int = 4) -> list[dict]:
            return [
                {
                    "order_id": "1",
                    "hn": "HN1",
                    "patient_name": "Patient One",
                    "ordered_at": "2026-04-22T10:00:00",
                    "priority": "routine",
                    "test_code": "CBC",
                    "context": {
                        "payload": {
                            "order_id": "1",
                            "patient_id": "1",
                            "test_code": "CBC",
                            "priority": "routine",
                            "duplicate_candidate": False,
                            "repeated_indicator": False,
                            "possible_duplicate": False,
                        }
                    },
                },
                {
                    "order_id": "2",
                    "hn": "HN1",
                    "patient_name": "Patient One",
                    "ordered_at": "2026-04-22T11:00:00",
                    "priority": "routine",
                    "test_code": "CBC",
                    "context": {
                        "payload": {
                            "order_id": "2",
                            "patient_id": "1",
                            "test_code": "CBC",
                            "priority": "routine",
                            "duplicate_candidate": True,
                            "repeated_indicator": True,
                            "possible_duplicate": True,
                        }
                    },
                },
            ]

        result = list_orders_flagged_by_current_rule(
            context_row_builder=fake_context_row_builder
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["orders"]), 1)
        self.assertEqual(result["orders"][0]["order_id"], "2")
        self.assertTrue(result["orders"][0]["duplicate_candidate"])

    def test_stewardship_summary_explains_reason_and_staff_action(self) -> None:
        summary = build_stewardship_ai_summary(
            {
                "order_id": "2",
                "hn": "HN1",
                "patient_name": "Patient One",
                "ordered_at": "2026-04-22T11:00:00",
                "priority": "routine",
                "test_code": "CBC",
                "reason": "Order payload contains a duplicate-order indicator.",
                "matched_fields": ["payload.duplicate_candidate", "payload.priority"],
            }
        )

        self.assertIn("Simulated AI stewardship summary", summary)
        self.assertIn("Order 2", summary)
        self.assertIn("CBC", summary)
        self.assertIn("Recommended staff action", summary)
        self.assertIn("requesting clinician", summary)


if __name__ == "__main__":
    unittest.main()
