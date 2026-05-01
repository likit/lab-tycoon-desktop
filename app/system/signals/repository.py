"""Small database bridge for order-derived rule context."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.system.models import LabOrder, LabOrderItem, engine

from .context_builder import build_rule_context


def find_prior_orders_for_duplicate_check(
    session: Session,
    order: LabOrder,
    window_hours: int = 4,
) -> list[LabOrder]:
    """Load earlier same-patient orders that are relevant to duplicate detection."""

    if window_hours < 0:
        raise ValueError("window_hours must be non-negative")
    if order.order_datetime is None:
        return []

    window_start = order.order_datetime - timedelta(hours=window_hours)
    query = (
        select(LabOrder)
        .where(LabOrder.customer_id == order.customer_id)
        .where(LabOrder.order_datetime >= window_start)
        .where(LabOrder.order_datetime < order.order_datetime)
        .options(selectinload(LabOrder.order_items).selectinload(LabOrderItem.test))
    )
    if order.id is not None:
        query = query.where(LabOrder.id != order.id)

    return list(session.scalars(query))


def build_rule_context_for_order_id(
    order_id: int | str,
    session: Session | None = None,
    window_hours: int = 4,
) -> dict[str, Any]:
    """Fetch one order and return rule-ready context for preview/evaluation."""

    owns_session = session is None
    active_session = session or Session(engine)
    try:
        order = active_session.scalar(
            select(LabOrder)
            .where(LabOrder.id == int(order_id))
            .options(selectinload(LabOrder.order_items).selectinload(LabOrderItem.test))
        )
        if order is None:
            raise ValueError(f"Order not found: {order_id}")

        prior_orders = find_prior_orders_for_duplicate_check(
            active_session,
            order,
            window_hours=window_hours,
        )
        return build_rule_context(order, prior_orders=prior_orders, window_hours=window_hours)
    finally:
        if owns_session:
            active_session.close()


def build_rule_context_rows_for_orders(
    session: Session | None = None,
    window_hours: int = 4,
) -> list[dict[str, Any]]:
    """Return order metadata plus rule-ready context for all stored orders."""

    owns_session = session is None
    active_session = session or Session(engine)
    try:
        query = (
            select(LabOrder)
            .options(
                selectinload(LabOrder.customer),
                selectinload(LabOrder.order_items).selectinload(LabOrderItem.test),
            )
            .order_by(LabOrder.order_datetime, LabOrder.id)
        )
        rows: list[dict[str, Any]] = []
        for order in active_session.scalars(query):
            prior_orders = find_prior_orders_for_duplicate_check(
                active_session,
                order,
                window_hours=window_hours,
            )
            context = build_rule_context(order, prior_orders=prior_orders, window_hours=window_hours)
            customer = getattr(order, "customer", None)
            rows.append(
                {
                    "order_id": str(order.id),
                    "hn": getattr(customer, "hn", ""),
                    "patient_name": getattr(customer, "fullname", ""),
                    "ordered_at": order.order_datetime.isoformat() if order.order_datetime else "",
                    "priority": getattr(order, "priority", "routine") or "routine",
                    "test_code": context["payload"].get("test_code"),
                    "context": context,
                }
            )
        return rows
    finally:
        if owns_session:
            active_session.close()
