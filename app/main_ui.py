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


def build_main_menu(*, simulation_enabled: bool = True) -> list[list[object]]:
    """Return navigation grouped by user-facing platform responsibility."""

    menu = [
        [
            "File",
            [
                "New Project...",
                "Open Project...",
                "Open Demo Project",
                "Save",
                "Save As...",
                "Close Project",
                "Program Settings...",
                "---",
                "Exit",
            ],
        ],
        [
            "LIS",
            [
                "Users", ["Register", "Manage Users"],
                "Tests",
                "Customers",
                "Orders",
                "Results",
                "Analyze",
                "Logs",
                "---",
                "Settings...",
            ],
        ],
        ["Simulation", [
            "New Project...", "Open Project...", "---",
            "Laboratory Design", ["Floor Plan", "!SOPs", "!Equipment & Resources"],
            "Workflows", ["Add Workflow...", "Open Workflow..."],
            "Scenarios", "!Run Simulation", "!Simulation Results",
        ]],
        [
            "Analytics",
            [
                "!Operational Analytics",
                "!Experiment Comparison",
                "!Export Data",
                "!Export to Power BI",
            ],
        ],
        ["Tools", ["Database Explorer", "Rule Preview", "SQL Editor"]],
        ["User", ["Profile", "Sign In", "Sign Out"]],
        ["Help", ["About Lab Tycoon"]],
    ]

    return menu if simulation_enabled else [section for section in menu if section[0] != "Simulation"]


def build_main_layout(
    resource_path: Callable[[str], str],
    context: MainWindowContext,
    *, simulation_enabled: bool = True,
) -> list[list[object]]:
    """Build the LIS landing screen around the three operational views."""

    cards = []
    for title, key, description, color in (
        ('Orders', '-ORDERS-', 'Receive and manage\nlaboratory orders', '#2563eb'),
        ('Customers', '-CUSTOMER-', 'Find and manage\ncustomer records', '#0f766e'),
        ('Results', '-RESULTS-', 'Review laboratory results\nand open result details', '#7c3aed'),
    ):
        cards.append(sg.Column([
            [sg.Button(title, key=key, size=(19, 2), font=('Arial', 17, 'bold'),
                       button_color=('white', color), border_width=0, pad=(8, 8))],
            [sg.Text(description, size=(27, 2), justification='center',
                     text_color='#64748b', font=('Arial', 11))],
        ], element_justification='center', pad=(8, 16)))
    dashboard = [
        [sg.Image(filename=resource_path('logo.png'), pad=(0, (18, 12)))],
        [sg.Text('Laboratory Information System', font=('Arial', 24, 'bold'))],
        [sg.Text('Orders, customer records, and results in one place.',
                 font=('Arial', 12), text_color='#64748b', pad=(0, (4, 12)))],
        cards,
        [sg.HorizontalSeparator(pad=(0, (12, 12)))],
        [sg.Text('', key='-ACTIVE-PROJECT-', text_color='#64748b',
                 visible=simulation_enabled and context.has_active_project)],
        [sg.Button('Sign In', key='-SIGNIN-'),
         sg.Button('Sign Out', key='-SIGNOUT-', visible=False),
         sg.Push(), sg.Exit(size=(8, 1), button_color=DANGER_BUTTON_COLOR)],
    ]
    return [
        [sg.Menu(build_main_menu(simulation_enabled=simulation_enabled), key='-MAIN-MENU-')],
        [sg.Push(), sg.Column(dashboard, element_justification='center', pad=(24, 16)), sg.Push()],
    ]


def update_context_elements(window: sg.Window, context: MainWindowContext, *, simulation_enabled=True) -> None:
    """Refresh the active-project indicator in the platform shell."""

    label = context.project_name + (" *" if context.modified else "")
    window["-ACTIVE-PROJECT-"].update("Simulation project: " + label,
        visible=simulation_enabled and context.has_active_project)
