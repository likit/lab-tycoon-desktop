"""Duplicate-order signal extraction.

This module intentionally answers only a data question: whether a current
order resembles a recent earlier order for the same patient and test.
Policy decisions stay in the rule layer.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable


def detect_duplicate_candidate(order: Any, prior_orders: Iterable[Any], window_hours: int = 4) -> bool:
    """Return whether an order repeats a patient/test within the prior time window."""

    if window_hours < 0:
        raise ValueError("window_hours must be non-negative")

    order_datetime = getattr(order, "order_datetime", None)
    if order_datetime is None:
        return False

    patient_id = _patient_id(order)
    test_codes = _test_codes(order)
    if patient_id is None or not test_codes:
        return False

    window_start = order_datetime - timedelta(hours=window_hours)
    order_id = getattr(order, "id", None)

    for prior_order in prior_orders:
        if order_id is not None and getattr(prior_order, "id", None) == order_id:
            continue
        if _patient_id(prior_order) != patient_id:
            continue

        prior_datetime = getattr(prior_order, "order_datetime", None)
        if prior_datetime is None or not (window_start <= prior_datetime < order_datetime):
            continue

        if test_codes.intersection(_test_codes(prior_order)):
            return True

    return False


def duplicated_test_codes(order: Any, prior_orders: Iterable[Any], window_hours: int = 4) -> set[str]:
    """Return current-order test codes that repeat in matching prior orders."""

    if window_hours < 0:
        raise ValueError("window_hours must be non-negative")

    order_datetime = getattr(order, "order_datetime", None)
    if order_datetime is None:
        return set()

    patient_id = _patient_id(order)
    current_codes = _test_codes(order)
    if patient_id is None or not current_codes:
        return set()

    window_start = order_datetime - timedelta(hours=window_hours)
    order_id = getattr(order, "id", None)
    duplicates: set[str] = set()

    for prior_order in prior_orders:
        if order_id is not None and getattr(prior_order, "id", None) == order_id:
            continue
        if _patient_id(prior_order) != patient_id:
            continue

        prior_datetime = getattr(prior_order, "order_datetime", None)
        if prior_datetime is None or not (window_start <= prior_datetime < order_datetime):
            continue

        duplicates.update(current_codes.intersection(_test_codes(prior_order)))

    return duplicates


def order_test_codes(order: Any) -> list[str]:
    """Return stable test codes from an order's items."""

    return sorted(_test_codes(order))


def order_patient_id(order: Any) -> str | None:
    """Return the patient/customer identifier used by duplicate detection."""

    return _patient_id(order)


def _patient_id(order: Any) -> str | None:
    customer_id = getattr(order, "customer_id", None)
    if customer_id is not None:
        return str(customer_id)

    customer = getattr(order, "customer", None)
    if customer is not None and getattr(customer, "id", None) is not None:
        return str(customer.id)

    return None


def _test_codes(order: Any) -> set[str]:
    codes: set[str] = set()
    for item in getattr(order, "order_items", []) or []:
        test = getattr(item, "test", None)
        code = getattr(test, "code", None)
        if code:
            codes.add(str(code))
    return codes
