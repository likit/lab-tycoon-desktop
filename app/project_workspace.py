"""Read-only project workspace shown after an .ltp project is opened."""

from __future__ import annotations

from datetime import datetime

import FreeSimpleGUI as sg
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.project import LabTycoonProject
from app.system.floor_view import create_floor_view_window
from app.system.models import Customer, LabOrder, LabOrderItem, engine
from app.ui_styles import DANGER_BUTTON_COLOR


ORDER_HEADINGS = ["ID", "HN", "Patient", "Ordered At", "Priority", "Status", "Items"]
RESULT_HEADINGS = [
    "Item ID",
    "Order ID",
    "HN",
    "Patient",
    "Test",
    "Result",
    "Reported At",
    "Approved At",
]
CUSTOMER_HEADINGS = ["ID", "HN", "Name", "Gender", "Date of Birth", "Address"]
OVERVIEW_ROW_LIMIT = 250


def _format_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _order_status(order: LabOrder) -> str:
    if order.approved_at:
        return "APPROVED"
    if order.rejected_at:
        return "REJECTED"
    if order.cancelled_at:
        return "CANCELLED"
    if order.received_at:
        return "RECEIVED"
    return "PENDING"


def load_order_rows(limit: int = OVERVIEW_ROW_LIMIT) -> list[list[object]]:
    """Return a bounded snapshot of the most recent LIS orders."""

    order_rows: list[list[object]] = []
    with Session(engine) as session:
        query = (
            select(LabOrder)
            .options(selectinload(LabOrder.customer), selectinload(LabOrder.order_items))
            .order_by(LabOrder.id.desc())
            .limit(limit)
        )
        for order in session.scalars(query):
            order_rows.append(
                [
                    order.id,
                    order.customer.hn if order.customer else "",
                    order.customer.fullname if order.customer else "",
                    _format_datetime(order.order_datetime),
                    order.priority,
                    _order_status(order),
                    len(order.order_items),
                ]
            )

    return order_rows


def load_result_rows(limit: int = OVERVIEW_ROW_LIMIT) -> list[list[object]]:
    """Return a bounded snapshot of the most recent LIS result items."""

    result_rows: list[list[object]] = []
    with Session(engine) as session:
        query = (
            select(LabOrderItem)
            .options(
                selectinload(LabOrderItem.test),
                selectinload(LabOrderItem.order).selectinload(LabOrder.customer),
            )
            .order_by(LabOrderItem.id.desc())
            .limit(limit)
        )
        for item in session.scalars(query):
            order = item.order
            customer = order.customer if order else None
            result_rows.append(
                [
                    item.id,
                    item.order_id,
                    customer.hn if customer else "",
                    customer.fullname if customer else "",
                    item.test.code if item.test else "",
                    item.value_string,
                    _format_datetime(item.reported_at),
                    _format_datetime(item.approved_at),
                ]
            )
    return result_rows


def load_order_and_result_rows() -> tuple[list[list[object]], list[list[object]]]:
    """Return bounded order and result snapshots for compatibility."""

    return load_order_rows(), load_result_rows()


def load_customer_rows(limit: int = OVERVIEW_ROW_LIMIT) -> list[list[object]]:
    """Return a bounded snapshot of LIS customers."""

    with Session(engine) as session:
        customers = session.scalars(select(Customer).order_by(Customer.id.desc()).limit(limit))
        return [
            [
                customer.id,
                customer.hn,
                customer.fullname,
                customer.gender,
                customer.dob.isoformat() if customer.dob else "",
                customer.address or "",
            ]
            for customer in customers
        ]


def create_project_workspace_window(project: LabTycoonProject) -> None:
    """Display tabbed LIS orders, results, and customers."""
    table_options = {
        "auto_size_columns": False,
        "alternating_row_color": "#eff6ff",
        "expand_x": True,
        "expand_y": True,
        "num_rows": 8,
    }
    layout = [
        [
            sg.Text(project.name, font=("Arial", 19, "bold")),
            sg.Push(),
            sg.Text("Project overview", text_color="#64748b"),
            sg.Button(
                "◇  Floor View",
                key="-OPEN-FLOOR-VIEW-",
                size=(14, 1),
                button_color=("white", "#2563eb"),
                tooltip="Open the editable laboratory floor",
            ),
        ],
        [
            sg.TabGroup(
                [[
                    sg.Tab("Orders", [
                        [sg.Text("Loading recent orders…", key="-PROJECT-ORDERS-STATUS-")],
                        [sg.Table(
                        values=[],
                        headings=ORDER_HEADINGS,
                        col_widths=[10, 14, 22, 20, 10, 12, 8],
                        key="-PROJECT-ORDERS-",
                        **table_options,
                    )]], key="-TAB-ORDERS-"),
                    sg.Tab("Results", [
                        [sg.Text("Results load when this tab is opened.", key="-PROJECT-RESULTS-STATUS-")],
                        [sg.Table(
                        values=[],
                        headings=RESULT_HEADINGS,
                        col_widths=[10, 10, 14, 22, 12, 16, 20, 20],
                        key="-PROJECT-RESULTS-",
                        **table_options,
                    )]], key="-TAB-RESULTS-"),
                    sg.Tab("Customers", [
                        [sg.Text("Customers load when this tab is opened.", key="-PROJECT-CUSTOMERS-STATUS-")],
                        [sg.Table(
                            values=[],
                            headings=CUSTOMER_HEADINGS,
                            col_widths=[10, 16, 24, 10, 14, 40],
                            key="-PROJECT-CUSTOMERS-",
                            **table_options,
                        )],
                    ], key="-TAB-CUSTOMERS-"),
                ]],
                key="-PROJECT-TABS-",
                enable_events=True,
                expand_x=True,
                expand_y=True,
            )
        ],
        [sg.Push(), sg.CloseButton("Close", button_color=DANGER_BUTTON_COLOR)],
    ]
    window = sg.Window(
        f"Lab Tycoon — {project.name}",
        layout,
        resizable=True,
        finalize=True,
        size=(1220, 900),
    )
    loaded_tabs = {"-TAB-ORDERS-"}

    window.refresh()
    order_rows = load_order_rows()
    window["-PROJECT-ORDERS-"].update(values=order_rows)
    window["-PROJECT-ORDERS-STATUS-"].update(
        f"Showing {len(order_rows)} most recent orders (maximum {OVERVIEW_ROW_LIMIT})."
    )

    try:
        while True:
            event, _values = window.read()
            if event in (sg.WIN_CLOSED, "Close"):
                break
            if event == "-OPEN-FLOOR-VIEW-":
                create_floor_view_window(project)
                continue
            if event == "-PROJECT-TABS-":
                selected_tab = _values.get("-PROJECT-TABS-")
                if selected_tab == "-TAB-RESULTS-" and selected_tab not in loaded_tabs:
                    result_rows = load_result_rows()
                    window["-PROJECT-RESULTS-"].update(values=result_rows)
                    window["-PROJECT-RESULTS-STATUS-"].update(
                        f"Showing {len(result_rows)} most recent results (maximum {OVERVIEW_ROW_LIMIT})."
                    )
                    loaded_tabs.add(selected_tab)
                elif selected_tab == "-TAB-CUSTOMERS-" and selected_tab not in loaded_tabs:
                    customer_rows = load_customer_rows()
                    window["-PROJECT-CUSTOMERS-"].update(values=customer_rows)
                    window["-PROJECT-CUSTOMERS-STATUS-"].update(
                        f"Showing {len(customer_rows)} most recent customers (maximum {OVERVIEW_ROW_LIMIT})."
                    )
                    loaded_tabs.add(selected_tab)
    finally:
        window.close()
