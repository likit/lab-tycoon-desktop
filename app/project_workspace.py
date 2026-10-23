"""Read-only project workspace shown after an .ltp project is opened."""

from __future__ import annotations

from datetime import datetime
import json

import FreeSimpleGUI as sg
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.windows import login_required
from app.project import LabTycoonProject
from app.simulation_runs import run_project_scenario
from app.system.floor_view import create_floor_view_window
from app.system.models import Customer, LabOrder, LabOrderItem, SimulationOrder, SimulationPatient, SimulationRun, engine
from app.system.storage import WorkflowDocument
from app.system.workflow_editor import create_workflow_window
from app.ui_styles import create_window, DANGER_BUTTON_COLOR, bring_window_to_front


ORDER_HEADINGS = ["ID", "HN", "Patient", "Ordered At", "Priority", "Status", "Items", "Source", "Scenario", "Run"]
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
CUSTOMER_HEADINGS = ["ID", "HN", "Name", "Gender", "Date of Birth", "Address", "Source", "Scenario", "Run"]
OVERVIEW_ROW_LIMIT = 250


@login_required
def add_project_workflow(project: LabTycoonProject) -> str | None:
    """Prompt for a new workflow name and return its project-owned ID."""

    name = sg.popup_get_text("Workflow name", title="New Project Workflow")
    if name is None:
        return None
    try:
        return project.add_workflow(name).workflow_id
    except ValueError as exc:
        sg.popup_error(str(exc))
        return None


@login_required
def choose_project_workflow(project: LabTycoonProject) -> str | None:
    """Choose an existing project workflow."""

    labels = {f"{item.name} [{item.workflow_id[:8]}]": item.workflow_id for item in project.workflows}
    layout = [
        [sg.Text("Choose a workflow for this laboratory")],
        [sg.Listbox(list(labels), default_values=[next(iter(labels))], key="-WORKFLOW-CHOICE-", size=(42, 7))],
        [sg.Button("Open"), sg.Button("Cancel")],
    ]
    dialog = create_window("Project Workflows", layout, modal=True, finalize=True)
    bring_window_to_front(dialog)
    try:
        while True:
            event, values = dialog.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                return None
            if event == "Open":
                selected = values.get("-WORKFLOW-CHOICE-", [])
                if selected:
                    return labels[selected[0]]
    finally:
        dialog.close()


@login_required
def open_project_workflow(project: LabTycoonProject, workflow_id: str = "main") -> None:
    """Open one project-backed workflow in the existing editor."""

    selected = project.get_workflow(workflow_id)
    if selected is None:
        raise ValueError("The selected workflow is no longer available.")
    document = WorkflowDocument(selected.workflow_id, selected.name, selected.workflow)
    create_workflow_window(
        current_document=document,
        on_change=project.mark_dirty,
        equipment_instances=project.floor.equipment_instances,
        run_simulation=(lambda scenario_id, count, seed: run_project_scenario(
            project, scenario_id=scenario_id, orders_number=count, random_seed=seed,
        )) if workflow_id == "main" else None,
    )


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


def load_order_rows(
    limit: int = OVERVIEW_ROW_LIMIT,
    *,
    project_id: str | None = None,
    source: str = "All",
    scenario_id: str | None = None,
    run_id: str | None = None,
) -> list[list[object]]:
    """Return LIS and project simulation orders with explicit provenance filters."""

    dated_rows: list[tuple[datetime | None, list[object]]] = []
    with Session(engine) as session:
        if source != "Simulation" and scenario_id is None and run_id is None:
            query = (
                select(LabOrder)
                .options(selectinload(LabOrder.customer), selectinload(LabOrder.order_items))
                .order_by(LabOrder.id.desc())
                .limit(limit)
            )
            for order in session.scalars(query):
                dated_rows.append((order.order_datetime, [
                    order.id,
                    order.customer.hn if order.customer else "",
                    order.customer.fullname if order.customer else "",
                    _format_datetime(order.order_datetime),
                    order.priority,
                    _order_status(order),
                    len(order.order_items),
                    "LIS", "", "",
                ]))
        if project_id is not None and source != "LIS":
            simulation_query = (
                select(SimulationOrder)
                .join(SimulationOrder.run)
                .options(selectinload(SimulationOrder.run), selectinload(SimulationOrder.patient))
                .where(SimulationRun.project_id == project_id)
                .order_by(SimulationOrder.id.desc())
                .limit(limit)
            )
            if scenario_id is not None:
                simulation_query = simulation_query.where(SimulationRun.scenario_id == scenario_id)
            if run_id is not None:
                simulation_query = simulation_query.where(SimulationRun.id == run_id)
            for order in session.scalars(simulation_query):
                dated_rows.append((order.ordered_at, [
                    order.order_id,
                    order.patient.hn if order.patient else order.patient_id,
                    order.patient.fullname if order.patient else f"Simulated Patient {order.patient_id}",
                    _format_datetime(order.ordered_at),
                    order.priority,
                    "CREATED",
                    len(json.loads(order.test_codes_json)),
                    "Simulation",
                    order.run.scenario_name,
                    order.run_id[:8],
                ]))

    dated_rows.sort(key=lambda item: item[0].replace(tzinfo=None) if item[0] else datetime.min, reverse=True)
    return [row for _, row in dated_rows[:limit]]


def load_simulation_runs(project_id: str) -> list[SimulationRun]:
    """List runs for filtering one project's generated orders."""

    with Session(engine) as session:
        return list(session.scalars(
            select(SimulationRun).where(SimulationRun.project_id == project_id)
            .order_by(SimulationRun.started_at.desc())
        ))


def load_result_rows(
    limit: int = OVERVIEW_ROW_LIMIT, *, source: str = "All",
    scenario_id: str | None = None, run_id: str | None = None,
) -> list[list[object]]:
    """Return a bounded snapshot of the most recent LIS result items."""

    if source == "Simulation" or scenario_id is not None or run_id is not None:
        return []  # Simulation results are not persisted yet.
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


def load_customer_rows(
    limit: int = OVERVIEW_ROW_LIMIT, *, project_id: str | None = None,
    source: str = "All", scenario_id: str | None = None, run_id: str | None = None,
) -> list[list[object]]:
    """Return LIS and project mock patients using the shared provenance filters."""

    rows: list[list[object]] = []
    with Session(engine) as session:
        if source != "Simulation" and scenario_id is None and run_id is None:
            customers = session.scalars(select(Customer).order_by(Customer.id.desc()).limit(limit))
            rows.extend([
                customer.id, customer.hn, customer.fullname, customer.gender,
                customer.dob.isoformat() if customer.dob else "", customer.address or "",
                "LIS", "", "",
            ] for customer in customers)
        if project_id is not None and source != "LIS":
            query = (
                select(SimulationPatient)
                .join(SimulationPatient.run)
                .options(selectinload(SimulationPatient.run))
                .where(SimulationRun.project_id == project_id)
                .order_by(SimulationPatient.id.desc())
                .limit(limit)
            )
            if scenario_id is not None:
                query = query.where(SimulationRun.scenario_id == scenario_id)
            if run_id is not None:
                query = query.where(SimulationRun.id == run_id)
            rows.extend([
                patient.patient_id, patient.hn, patient.fullname, "", "", "",
                "Simulation", patient.run.scenario_name, patient.run_id[:8],
            ] for patient in session.scalars(query))
    return rows[:limit]


@login_required
def open_project_scenarios(project, workflow_id='main'):
    from app.system.scenario_editor import open_scenario_manager
    selected = project.get_workflow(workflow_id)
    if selected is None:
        raise ValueError('The selected workflow is no longer available.')
    open_scenario_manager(selected.workflow, on_change=project.mark_dirty,
        run_simulation=(lambda scenario_id, count, seed: run_project_scenario(
            project, scenario_id=scenario_id, orders_number=count, random_seed=seed))
            if workflow_id == 'main' else None)


@login_required
def open_project_lab(project: LabTycoonProject, *, home_window=None, on_save=None) -> None:
    """Use the floor as the main lab workspace and restore home on return."""
    if home_window is not None:
        home_window.hide()
    try:
        create_floor_view_window(project, main_workspace=True, on_save=on_save)
    finally:
        if home_window is not None:
            home_window.un_hide()


def open_lab_section(project: LabTycoonProject, section: str, *, workflow_id=None) -> None:
    """Open a secondary view without constructing another floor window."""
    if section == 'Workflow':
        open_project_workflow(project, workflow_id)
    elif section in ('Orders', 'Customers', 'Results'):
        create_project_workspace_window(project, initial_tab=section, show_navigation=False)
    else:
        raise ValueError(f'Unknown lab section: {section}')


def create_project_workspace_window(project: LabTycoonProject, *, initial_tab: str = "Orders",
                                    show_navigation: bool = True) -> None:
    """Display tabbed LIS orders, results, and customers."""
    runs = load_simulation_runs(project.project_id)
    scenario_labels = {"All scenarios": None}
    scenario_labels.update({
        f"{scenario.name} [{scenario.scenario_id[:8]}]": scenario.scenario_id
        for scenario in project.workflow.scenarios
    })
    run_labels = {"All runs": None}
    run_labels.update({
        f"{run.scenario_name} — {run.started_at:%Y-%m-%d %H:%M} [{run.id[:8]}]": run.id
        for run in runs
    })
    latest_run = runs[0] if runs else None
    default_source = "Simulation" if latest_run else "All"
    default_scenario = next(
        (label for label, identifier in scenario_labels.items() if latest_run and identifier == latest_run.scenario_id),
        "All scenarios",
    )
    default_run = next(
        (label for label, identifier in run_labels.items() if latest_run and identifier == latest_run.id),
        "All runs",
    )
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
                "◇  Floor Plan",
                key="-OPEN-FLOOR-VIEW-",
                visible=show_navigation,
                size=(14, 1),
                button_color=("white", "#2563eb"),
            ),
            sg.Button(
                "◇  Workflow",
                key="-OPEN-WORKFLOW-",
                visible=show_navigation,
                size=(14, 1),
                button_color=("white", "#2563eb"),
            ),
        ],
        [
            sg.Text("Source"),
            sg.Combo(["All", "LIS", "Simulation"], default_value=default_source, key="-ORDER-SOURCE-", readonly=True, enable_events=True, size=(13, 1)),
            sg.Text("Scenario"),
            sg.Combo(list(scenario_labels), default_value=default_scenario, key="-ORDER-SCENARIO-", readonly=True, enable_events=True, size=(25, 1)),
            sg.Text("Run"),
            sg.Combo(list(run_labels), default_value=default_run, key="-ORDER-RUN-", readonly=True, enable_events=True, size=(38, 1)),
            sg.Button("Refresh", key="-ORDER-REFRESH-"),
        ],
        [
            sg.TabGroup(
                [[
                    sg.Tab("Orders", [
                        [sg.Text("Loading recent orders…", key="-PROJECT-ORDERS-STATUS-")],
                        [sg.Table(
                        values=[],
                        headings=ORDER_HEADINGS,
                        col_widths=[12, 14, 22, 20, 10, 12, 8, 12, 20, 10],
                        key="-PROJECT-ORDERS-",
                        **table_options,
                    )]], key="-TAB-ORDERS-"),
                    sg.Tab("Results", [
                        [sg.Text("Loading results…", key="-PROJECT-RESULTS-STATUS-")],
                        [sg.Table(
                        values=[],
                        headings=RESULT_HEADINGS,
                        col_widths=[10, 10, 14, 22, 12, 16, 20, 20],
                        key="-PROJECT-RESULTS-",
                        **table_options,
                    )]], key="-TAB-RESULTS-"),
                    sg.Tab("Customers", [
                        [sg.Text("Loading customers…", key="-PROJECT-CUSTOMERS-STATUS-")],
                        [sg.Table(
                            values=[],
                            headings=CUSTOMER_HEADINGS,
                            col_widths=[10, 16, 24, 10, 14, 28, 12, 20, 10],
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
    window = create_window(
        f"Lab Tycoon — {project.name}",
        layout,
        resizable=True,
        finalize=True,
        size=(1220, 900),
    )
    selected_filters = {
        "-ORDER-SOURCE-": default_source,
        "-ORDER-SCENARIO-": default_scenario,
        "-ORDER-RUN-": default_run,
    }

    def refresh_data(values: dict[str, object] | None = None) -> None:
        if values:
            selected_filters.update({key: values[key] for key in selected_filters if key in values})
        source = str(selected_filters["-ORDER-SOURCE-"])
        scenario_id = scenario_labels.get(str(selected_filters["-ORDER-SCENARIO-"]))
        run_id = run_labels.get(str(selected_filters["-ORDER-RUN-"]))
        order_rows = load_order_rows(
            project_id=project.project_id,
            source=source,
            scenario_id=scenario_id,
            run_id=run_id,
        )
        window["-PROJECT-ORDERS-"].update(values=order_rows)
        window["-PROJECT-ORDERS-STATUS-"].update(
            f"Showing {len(order_rows)} most recent orders (maximum {OVERVIEW_ROW_LIMIT})."
        )
        result_rows = load_result_rows(source=source, scenario_id=scenario_id, run_id=run_id)
        window["-PROJECT-RESULTS-"].update(values=result_rows)
        window["-PROJECT-RESULTS-STATUS-"].update(
            "Simulation results are not saved yet." if source == "Simulation" or scenario_id or run_id
            else f"Showing {len(result_rows)} most recent LIS results (maximum {OVERVIEW_ROW_LIMIT})."
        )
        customer_rows = load_customer_rows(
            project_id=project.project_id, source=source, scenario_id=scenario_id, run_id=run_id,
        )
        window["-PROJECT-CUSTOMERS-"].update(values=customer_rows)
        window["-PROJECT-CUSTOMERS-STATUS-"].update(
            f"Showing {len(customer_rows)} patients (maximum {OVERVIEW_ROW_LIMIT})."
        )

    tab_keys = {"Orders": "-TAB-ORDERS-", "Results": "-TAB-RESULTS-", "Customers": "-TAB-CUSTOMERS-"}
    window[tab_keys.get(initial_tab, "-TAB-ORDERS-")].select()
    window.refresh()
    refresh_data()

    try:
        while True:
            event, _values = window.read()
            if event in (sg.WIN_CLOSED, "Close"):
                break
            if event == "-OPEN-FLOOR-VIEW-":
                create_floor_view_window(project)
                continue
            if event == "-OPEN-WORKFLOW-":
                workflow_id = choose_project_workflow(project)
                if workflow_id is None:
                    continue
                open_project_workflow(project, workflow_id)
                scenario_labels = {"All scenarios": None}
                scenario_labels.update({
                    f"{scenario.name} [{scenario.scenario_id[:8]}]": scenario.scenario_id
                    for scenario in project.workflow.scenarios
                })
                runs = load_simulation_runs(project.project_id)
                run_labels = {"All runs": None}
                run_labels.update({
                    f"{run.scenario_name} — {run.started_at:%Y-%m-%d %H:%M} [{run.id[:8]}]": run.id
                    for run in runs
                })
                latest_run = runs[0] if runs else None
                selected_filters = {
                    "-ORDER-SOURCE-": "Simulation" if latest_run else "All",
                    "-ORDER-SCENARIO-": next((label for label, identifier in scenario_labels.items() if latest_run and identifier == latest_run.scenario_id), "All scenarios"),
                    "-ORDER-RUN-": next((label for label, identifier in run_labels.items() if latest_run and identifier == latest_run.id), "All runs"),
                }
                window["-ORDER-SOURCE-"].update(value=selected_filters["-ORDER-SOURCE-"])
                window["-ORDER-SCENARIO-"].update(values=list(scenario_labels), value=selected_filters["-ORDER-SCENARIO-"])
                window["-ORDER-RUN-"].update(values=list(run_labels), value=selected_filters["-ORDER-RUN-"])
                refresh_data()
                continue
            if event in {"-ORDER-SOURCE-", "-ORDER-SCENARIO-", "-ORDER-RUN-", "-ORDER-REFRESH-"}:
                if event == "-ORDER-SOURCE-" and _values.get("-ORDER-SOURCE-") == "LIS":
                    _values["-ORDER-SCENARIO-"] = "All scenarios"
                    _values["-ORDER-RUN-"] = "All runs"
                    window["-ORDER-SCENARIO-"].update(value="All scenarios")
                    window["-ORDER-RUN-"].update(value="All runs")
                elif event in {"-ORDER-SCENARIO-", "-ORDER-RUN-"} and (
                    _values.get("-ORDER-SCENARIO-") != "All scenarios" or _values.get("-ORDER-RUN-") != "All runs"
                ):
                    _values["-ORDER-SOURCE-"] = "Simulation"
                    window["-ORDER-SOURCE-"].update(value="Simulation")
                refresh_data(_values)
                continue
    finally:
        window.close()
