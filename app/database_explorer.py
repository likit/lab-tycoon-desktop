"""Read-only explorer for the currently active LIS database."""

from __future__ import annotations

import FreeSimpleGUI as sg
from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.system.models import engine
from app.auth.windows import login_required
from app.ui_styles import DANGER_BUTTON_COLOR


PREVIEW_LIMIT = 100
SCHEMA_HEADINGS = ["Column", "Type", "Nullable", "Default", "Primary Key"]


def list_database_tables(database_engine: Engine) -> list[str]:
    """Return user tables in deterministic order."""

    return sorted(inspect(database_engine).get_table_names())


def inspect_database_table(
    database_engine: Engine,
    table_name: str,
    *,
    preview_limit: int = PREVIEW_LIMIT,
) -> tuple[list[list[object]], list[str], list[list[object]]]:
    """Return schema rows, preview headings, and bounded preview rows."""

    inspector = inspect(database_engine)
    if table_name not in inspector.get_table_names():
        raise ValueError(f"Unknown database table: {table_name}")
    schema_rows = [
        [
            column["name"],
            str(column["type"]),
            "Yes" if column.get("nullable", True) else "No",
            "" if column.get("default") is None else str(column["default"]),
            "Yes" if column.get("primary_key", False) else "No",
        ]
        for column in inspector.get_columns(table_name)
    ]
    reflected_table = Table(table_name, MetaData(), autoload_with=database_engine)
    with database_engine.connect() as connection:
        rows = connection.execute(select(reflected_table).limit(preview_limit)).fetchall()
    return schema_rows, [column.name for column in reflected_table.columns], [list(row) for row in rows]


def format_preview(headings: list[str], rows: list[list[object]]) -> str:
    """Format dynamic database columns as a compact tab-separated preview."""

    if not headings:
        return "No columns found."
    lines = ["\t".join(headings), "\t".join("—" for _heading in headings)]
    for row in rows:
        lines.append("\t".join("NULL" if value is None else str(value) for value in row))
    if not rows:
        lines.append("(table is empty)")
    return "\n".join(lines)


@login_required
def create_database_explorer_window() -> None:
    """Open a read-only schema and data browser for the active LIS database."""

    try:
        table_names = list_database_tables(engine)
    except SQLAlchemyError as exc:
        sg.popup_error(f"Database tables could not be loaded:\n{exc}", title="Database Explorer")
        return

    tree_data = sg.TreeData()
    tree_data.insert("", "tables", "Tables", [len(table_names)])
    for table_name in table_names:
        tree_data.insert("tables", f"table:{table_name}", table_name, [""])

    layout = [
        [sg.Text("Database Explorer", font=("Arial", 20, "bold"))],
        [
            sg.Frame(
                "Database objects",
                [[sg.Tree(
                    data=tree_data,
                    headings=["Count"],
                    key="-DBX-TREE-",
                    enable_events=True,
                    show_expanded=True,
                    col0_width=28,
                    num_rows=26,
                    expand_x=True,
                    expand_y=True,
                )]],
                expand_y=True,
            ),
            sg.VSeparator(),
            sg.Column(
                [
                    [sg.Text("Select a table", key="-DBX-TITLE-", font=("Arial", 15, "bold"))],
                    [sg.Text("Schema", font=("Arial", 13, "bold"))],
                    [sg.Table(
                        values=[],
                        headings=SCHEMA_HEADINGS,
                        key="-DBX-SCHEMA-",
                        auto_size_columns=True,
                        num_rows=9,
                        expand_x=True,
                    )],
                    [sg.Text(f"Preview (first {PREVIEW_LIMIT} rows)", font=("Arial", 13, "bold"))],
                    [sg.Multiline(
                        "Select a table from the tree.",
                        key="-DBX-PREVIEW-",
                        disabled=True,
                        font=("Courier", 11),
                        horizontal_scroll=True,
                        expand_x=True,
                        expand_y=True,
                    )],
                ],
                expand_x=True,
                expand_y=True,
            ),
        ],
        [sg.Push(), sg.CloseButton("Close", button_color=DANGER_BUTTON_COLOR)],
    ]
    window = sg.Window(
        "Database Explorer",
        layout,
        resizable=True,
        finalize=True,
        size=(1250, 820),
    )
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Close"):
                break
            if event == "-DBX-TREE-" and values.get("-DBX-TREE-"):
                selected_key = values["-DBX-TREE-"][0]
                if not str(selected_key).startswith("table:"):
                    continue
                table_name = str(selected_key).split(":", 1)[1]
                try:
                    schema_rows, headings, preview_rows = inspect_database_table(engine, table_name)
                except (SQLAlchemyError, ValueError) as exc:
                    sg.popup_error(f"Table could not be inspected:\n{exc}", title="Database Explorer")
                    continue
                window["-DBX-TITLE-"].update(table_name)
                window["-DBX-SCHEMA-"].update(values=schema_rows)
                window["-DBX-PREVIEW-"].update(format_preview(headings, preview_rows))
    finally:
        window.close()
