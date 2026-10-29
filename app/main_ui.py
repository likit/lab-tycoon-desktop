"""Main-window presentation helpers for the Lab Tycoon platform shell."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import FreeSimpleGUI as sg

from app.ui_styles import DANGER_BUTTON_COLOR


@dataclass(slots=True)
class MainWindowContext:
    """Active project information shown in the main workspace."""

    project_name: str = "No project open"
    laboratory_name: str = ""
    scenario_name: str = "Default"
    modified: bool = False
    has_active_project: bool = False

    @property
    def project_status(self) -> str:
        return "Modified" if self.modified else "Ready"


PROJECT_ACTIONS = {
    'New Project...', 'Open Project...', 'Open Demo Project',
    '-NEW-PROJECT-', '-OPEN-PROJECT-',
}
PROJECT_FREE_ACTIONS = PROJECT_ACTIONS | {
    'Exit', 'Program Settings...', 'Sign In', 'Sign Out', 'Profile',
    '-SIGNIN-', '-SIGNOUT-', 'About Lab Tycoon',
}


def project_action_allowed(event, has_active_project):
    return has_active_project or event in PROJECT_FREE_ACTIONS


def build_main_menu(*, simulation_enabled: bool = True, has_active_project: bool = True) -> list[list[object]]:
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

    if not has_active_project:
        def gate(items):
            return [gate(item) if isinstance(item, list) else
                    item if item.startswith('!') or item == '---' or
                    (index + 1 < len(items) and isinstance(items[index + 1], list)) or
                    project_action_allowed(item, False) else '!' + item
                    for index, item in enumerate(items)]
        menu = [[title, gate(items)] for title, items in menu]
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
                       button_color=('white', color), border_width=0, pad=(8, 8),
                       disabled=not context.has_active_project)],
            [sg.Text(description, size=(27, 2), justification='center',
                     text_color='#64748b', font=('Arial', 11))],
        ], element_justification='center', pad=(8, 16)))
    dashboard = [
        [sg.Image(filename=resource_path('logo.png'), pad=(0, (18, 12)))],
        [sg.Text('Laboratory Information System', font=('Arial', 24, 'bold'))],
        [sg.Text('Orders, customer records, and results in one place.',
                 font=('Arial', 12), text_color='#64748b', pad=(0, (4, 12)))],
        [sg.Text('', key='-ACTIVE-PROJECT-', font=('Arial', 20, 'bold'),
                 text_color='#1d4ed8', pad=(0, (8, 4)))],
        [sg.Text('', key='-PROJECT-STATUS-', text_color='#64748b')],
        [sg.Text('Use the File menu to create or open a project.', key='-PROJECT-REQUIRED-',
                 visible=not context.has_active_project, font=('Arial', 13, 'bold'))],
        cards,
        [sg.HorizontalSeparator(pad=(0, (12, 12)))],
        [sg.Button('Sign In', key='-SIGNIN-'),
         sg.Button('Sign Out', key='-SIGNOUT-', visible=False),
         sg.Push(), sg.Exit(size=(8, 1), button_color=DANGER_BUTTON_COLOR)],
    ]
    return [
        [sg.Menu(build_main_menu(simulation_enabled=simulation_enabled,
                                  has_active_project=context.has_active_project), key='-MAIN-MENU-')],
        [sg.Push(), sg.Column(dashboard, element_justification='center', pad=(24, 16)), sg.Push()],
    ]


def update_context_elements(window: sg.Window, context: MainWindowContext, *, simulation_enabled=True) -> None:
    """Refresh the active-project indicator in the platform shell."""

    label = 'Active project: ' + context.project_name if context.has_active_project else 'No active project'
    window['-ACTIVE-PROJECT-'].update(label)
    window['-PROJECT-STATUS-'].update(
        'Unsaved changes' if context.has_active_project and context.modified else
        'Ready' if context.has_active_project else 'A project is required to use the workspace.')
    window['-PROJECT-REQUIRED-'].update(visible=not context.has_active_project)
    for key in ('-ORDERS-', '-CUSTOMER-', '-RESULTS-'):
        window[key].update(disabled=not context.has_active_project)
    window['-MAIN-MENU-'].update(build_main_menu(
        simulation_enabled=simulation_enabled, has_active_project=context.has_active_project))
