"""LIS application settings windows."""

from __future__ import annotations

import FreeSimpleGUI as sg
from sqlalchemy.exc import SQLAlchemyError

from app.config import DATABASE_CONFIG, update_config_yaml
from app.database import DatabaseConfig, DatabaseConfigurationError, DatabaseManager
from app.system.models import initialize_db
from app.ui_styles import DANGER_BUTTON_COLOR


def _config_from_values(values: dict[str, object]) -> DatabaseConfig:
    driver_label = str(values.get("-DB-DRIVER-", "SQLite"))
    return DatabaseConfig(
        driver=driver_label.lower(),
        database_path=str(values.get("-DB-PATH-", "")).strip(),
    ).normalized()


def _show_restart_required_dialog() -> bool:
    """Return True when the user asks Lab Tycoon to close now."""

    layout = [
        [sg.Text("Restart Required", font=("Arial", 17, "bold"))],
        [
            sg.Text(
                "Restart Lab Tycoon to use the newly selected LIS database.",
                size=(54, 2),
            )
        ],
        [
            sg.Push(),
            sg.Button("Later"),
            sg.Button("Close App Now", button_color=DANGER_BUTTON_COLOR),
        ],
    ]
    dialog = sg.Window("Restart Required", layout, modal=True)
    try:
        event, _values = dialog.read()
        return event == "Close App Now"
    finally:
        dialog.close()


def create_lis_settings_window() -> bool:
    """Edit application-level LIS database settings for the next restart."""

    layout = [
        [sg.Text("LIS Settings", font=("Arial", 20, "bold"))],
        [
            sg.Frame(
                "Database",
                [
                    [
                        sg.Text("Driver", size=(14, 1)),
                        sg.Combo(
                            ["SQLite"],
                            default_value="SQLite",
                            readonly=True,
                            key="-DB-DRIVER-",
                            size=(24, 1),
                        ),
                    ],
                    [
                        sg.Text("Database file", size=(14, 1)),
                        sg.Input(DATABASE_CONFIG.database_path, key="-DB-PATH-", expand_x=True),
                        sg.FileBrowse(
                            "Browse...",
                            target="-DB-PATH-",
                            file_types=(("SQLite Database", "*.db *.sqlite *.sqlite3"), ("All Files", "*.*")),
                        ),
                    ],
                    [
                        sg.Button("Test Connection", key="-DB-TEST-"),
                        sg.Button("Create New Database...", key="-DB-CREATE-"),
                        sg.Text("", key="-DB-STATUS-", size=(48, 1)),
                    ],
                ],
                expand_x=True,
            )
        ],
        [
            sg.Push(),
            sg.Button("Cancel", button_color=DANGER_BUTTON_COLOR),
            sg.Button("Save", key="-DB-SAVE-"),
        ],
    ]
    window = sg.Window("LIS Settings", layout, modal=True, resizable=True, finalize=True)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, "Cancel"):
                break
            if event == "-DB-TEST-":
                try:
                    DatabaseManager(_config_from_values(values)).test_connection()
                except (DatabaseConfigurationError, OSError, SQLAlchemyError) as exc:
                    window["-DB-STATUS-"].update(f"Connection failed: {exc}", text_color="#b91c1c")
                else:
                    window["-DB-STATUS-"].update("Connection successful.", text_color="#15803d")
            elif event == "-DB-CREATE-":
                selected_path = sg.popup_get_file(
                    "Create SQLite Database",
                    save_as=True,
                    no_window=True,
                    default_extension=".db",
                    file_types=(("SQLite Database", "*.db"),),
                )
                if not selected_path:
                    continue
                config = DatabaseConfig.default_sqlite(selected_path)
                manager = DatabaseManager(config)
                try:
                    created_path = manager.create_database()
                    new_engine = manager.create_orm_engine()
                    try:
                        initialize_db(new_engine)
                    finally:
                        new_engine.dispose()
                except (DatabaseConfigurationError, OSError, SQLAlchemyError) as exc:
                    sg.popup_error(f"Database could not be created:\n{exc}", title="LIS Settings")
                    continue
                window["-DB-PATH-"].update(str(created_path))
                update_config_yaml(database=config.to_dict())
                return _show_restart_required_dialog()
            elif event == "-DB-SAVE-":
                config = _config_from_values(values)
                try:
                    DatabaseManager(config).test_connection()
                except (DatabaseConfigurationError, OSError) as exc:
                    sg.popup_error(f"Database settings are invalid:\n{exc}", title="LIS Settings")
                    continue
                update_config_yaml(database=config.to_dict())
                return _show_restart_required_dialog()
    finally:
        window.close()
    return False
