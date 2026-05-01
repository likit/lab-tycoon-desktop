"""Order-source bridge for rule context and duplicate-signal evaluation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable

from app.system.order_sources import OrderSource, get_active_order_source

from .context_builder import build_rule_context


def find_prior_orders_for_duplicate_check(
    order: Any,
    candidate_orders: Iterable[Any],
    window_hours: int = 4,
) -> list[Any]:
    """Return earlier same-patient orders from the provided order collection."""

    if window_hours < 0:
        raise ValueError("window_hours must be non-negative")

    order_datetime = getattr(order, "order_datetime", None)
    if order_datetime is None:
        return []

    customer_id = _order_patient_id(order)
    if customer_id is None:
        return []

    window_start = order_datetime - timedelta(hours=window_hours)
    order_id = str(getattr(order, "id", ""))
    prior_orders = []
    for candidate in candidate_orders:
        if str(getattr(candidate, "id", "")) == order_id:
            continue
        if _order_patient_id(candidate) != customer_id:
            continue

        candidate_time = getattr(candidate, "order_datetime", None)
        if candidate_time is None or not (window_start <= candidate_time < order_datetime):
            continue
        prior_orders.append(candidate)
    return prior_orders


def build_rule_context_for_order_id(
    order_id: int | str,
    order_source: OrderSource | None = None,
    window_hours: int = 4,
) -> dict[str, Any]:
    """Fetch one order from the selected source and return rule-ready context."""

    source = order_source or get_active_order_source()
    orders = source.list_orders()
    order = source.get_order(order_id)
    if order is None:
        raise ValueError(f"Order not found: {order_id}")

    prior_orders = find_prior_orders_for_duplicate_check(order, orders, window_hours)
    return build_rule_context(order, prior_orders=prior_orders, window_hours=window_hours)


def build_rule_context_rows_for_orders(
    order_source: OrderSource | None = None,
    window_hours: int = 4,
) -> list[dict[str, Any]]:
    """Return order metadata plus rule-ready context for all orders in the source."""

    source = order_source or get_active_order_source()
    orders = source.list_orders()
    rows: list[dict[str, Any]] = []
    for order in orders:
        prior_orders = find_prior_orders_for_duplicate_check(order, orders, window_hours)
        context = build_rule_context(order, prior_orders=prior_orders, window_hours=window_hours)
        customer = getattr(order, "customer", None)
        rows.append(
            {
                "order_id": str(getattr(order, "id", "")),
                "hn": getattr(customer, "hn", ""),
                "patient_name": getattr(customer, "fullname", ""),
                "ordered_at": getattr(order, "order_datetime", None).isoformat()
                if getattr(order, "order_datetime", None)
                else "",
                "priority": getattr(order, "priority", "routine") or "routine",
                "test_code": context["payload"].get("test_code"),
                "context": context,
            }
        )
    return rows


def _order_patient_id(order: Any) -> str | None:
    customer_id = getattr(order, "customer_id", None)
    if customer_id is not None:
        return str(customer_id)
    customer = getattr(order, "customer", None)
    if customer is not None and getattr(customer, "id", None) is not None:
        return str(customer.id)
    return None
