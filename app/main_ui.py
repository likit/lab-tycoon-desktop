"""Main-window presentation helpers for the Lab Tycoon platform shell."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import FreeSimpleGUI as sg

from app.ui_styles import DANGER_BUTTON_COLOR


@dataclass(slots=True)
class MainWindowContext:
    """Display-only bridge for a future loaded Lab Tycoon project."""

    project_name: str = "Demo Laboratory"
    laboratory_name: str = "Demo Laboratory"
    scenario_name: str = "Default"
    modified: bool = False
    has_active_project: bool = False

    @property
    def project_status(self) -> str:
        return "Modified" if self.modified else "Ready"


def build_main_menu() -> list[list[object]]:
    """Return navigation grouped by user-facing platform responsibility."""

    return [
        [
            "File",
            [
                "New Project...",
                "Open Project...",
                "Open Demo Project",
                "Save",
                "Save As...",
                "Close Project",
                "---",
                "Exit",
            ],
        ],
        [
            "LIS",
            [
                ["Users", ["Register", "Manage Users"]],
                "Tests",
                "Customers",
                "Orders",
                "Analyze",
                "Logs",
                "---",
                "Settings...",
            ],
        ],
        ["Simulation", ["!Run Simulation", "!Scenarios", "!Simulation Results"]],
        ["Workflow", ["Add Workflow...", "Open Workflow..."]],
        [
            "Analytics",
            [
                "!Operational Analytics",
                "!Experiment Comparison",
                "!Export Data",
                "!Export to Power BI",
            ],
        ],
        [
            "Laboratory Design",
            ["Floor Plan", "!SOPs", "!Equipment & Resources"],
        ],
        ["Tools", ["Database Explorer", "Rule Preview", "SQL Editor"]],
        ["User", ["Profile", "Sign In", "Sign Out"]],
        ["Help", ["About Lab Tycoon"]],
    ]


def build_main_layout(
    resource_path: Callable[[str], str],
    context: MainWindowContext,
) -> list[list[object]]:
    """Build the beginner-friendly project landing screen."""

    primary_button = ("white", "#2563eb")
    project_actions = [
        [
            sg.Button(
                "＋  New Project...",
                key="-NEW-PROJECT-",
                size=(15, 1),
                button_color=primary_button,
                border_width=0,
                tooltip="Create a new Lab Tycoon project",
            ),
            sg.Button(
                "📂  Open Project...",
                key="-OPEN-PROJECT-",
                size=(15, 1),
                tooltip="Open an existing .ltp project",
            ),
        ],
        [
            sg.Button(
                "🧪  Go to Lab",
                key="-GO-TO-LAB-",
                size=(15, 1),
                button_color=("white", "#0f766e"),
                border_width=0,
                visible=context.has_active_project,
                tooltip="Open the active laboratory workspace",
            )
        ],
    ]
    dashboard = [
        [sg.Image(filename=resource_path("logo.png"), pad=((0, 0), (14, 4)))],
        [sg.Text("Laboratory Systems Platform", font=("Arial", 20, "bold"), pad=(0, (0, 2)))],
        [
            sg.Text(
                "Learn the LIS. Explore the laboratory. Understand its operation.",
                font=("Arial", 12),
                text_color="#64748b",
                pad=(0, (0, 14)),
            )
        ],
        [sg.Text("No project open", key="-ACTIVE-PROJECT-", font=("Arial", 12, "bold"), text_color="#64748b")],
        [
            sg.Column(
                project_actions,
                element_justification="center",
                pad=(0, (8, 20)),
            )
        ],
        [
            sg.Button("Sign In", key="-SIGNIN-"),
            sg.Button("Sign Out", key="-SIGNOUT-", visible=False),
            sg.Push(),
            sg.Exit(size=(8, 1), button_color=DANGER_BUTTON_COLOR),
        ],
        [
            sg.HorizontalSeparator(pad=(0, (10, 6))),
        ],
        [
            sg.Text(
                "Demo platform for teaching laboratory information systems and operations",
                font=("Arial", 10),
                text_color="#94a3b8",
            )
        ],
    ]
    return [
        [sg.Menu(build_main_menu())],
        [sg.Push(), sg.Column(dashboard, element_justification="center", pad=(24, 8)), sg.Push()],
    ]


def update_context_elements(window: sg.Window, context: MainWindowContext) -> None:
    """Refresh the active-project indicator in the platform shell."""

    label = context.project_name + (" *" if context.modified else "")
    window["-ACTIVE-PROJECT-"].update(label)
    window["-GO-TO-LAB-"].update(visible=context.has_active_project)
