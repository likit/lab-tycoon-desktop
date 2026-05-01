"""Build compact rule-evaluation context from LabTycoon orders."""

from __future__ import annotations

from typing import Any, Iterable

from .duplicate import (
    detect_duplicate_candidate,
    duplicated_test_codes,
    order_patient_id,
    order_test_codes,
)


def build_rule_context(
    order: Any,
    prior_orders: Iterable[Any] | None = None,
    window_hours: int = 4,
) -> dict[str, dict[str, object]]:
    """Return the compact payload expected by the rule-preview/evaluation path."""

    prior_order_list = list(prior_orders or [])
    duplicate_candidate = detect_duplicate_candidate(order, prior_order_list, window_hours)
    test_code = _select_test_code(order, prior_order_list, window_hours)

    payload: dict[str, object] = {
        "order_id": _string_or_none(getattr(order, "id", None)),
        "patient_id": order_patient_id(order),
        "test_code": test_code,
        "priority": getattr(order, "priority", "routine") or "routine",
        "duplicate_candidate": duplicate_candidate,
        "repeated_indicator": duplicate_candidate,
        "possible_duplicate": duplicate_candidate,
    }
    return {"payload": payload}


def _select_test_code(order: Any, prior_orders: list[Any], window_hours: int) -> str | None:
    duplicated_codes = duplicated_test_codes(order, prior_orders, window_hours)
    if duplicated_codes:
        return sorted(duplicated_codes)[0]

    test_codes = order_test_codes(order)
    return test_codes[0] if test_codes else None


def _string_or_none(value: object) -> str | None:
    return str(value) if value is not None else None
