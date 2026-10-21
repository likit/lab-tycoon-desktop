import os.path
import platform
import shutil
import sys

import FreeSimpleGUI as sg
import jwt
import keyring

from app.auth.windows import (create_signin_window,
                              create_profile_window,
                              create_register_window,
                              create_user_list_window)
from app.system.models import database_is_initialized, ensure_db_schema, initialize_db
from app.system.floor_view import create_floor_view_window
from app.main_ui import MainWindowContext, build_main_layout, update_context_elements
from app.lis_settings import create_lis_settings_window
from app.database_explorer import create_database_explorer_window
from app.project import LabTycoonProject, project_session
from app.project_workspace import add_project_workflow, choose_project_workflow, create_project_workspace_window, open_project_workflow
from app.project_serializer import ProjectFormatError, ProjectSerializer
from app.config import DATABASE_PATH, config_dict, secret_key, update_config_yaml
from app.auth.windows import SessionManager
from app.system.windows import *

session_manager = SessionManager()

base_url = os.path.dirname(os.path.abspath(__file__))

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        base_path = os.path.join(sys._MEIPASS, 'app')
    else:
        base_path = base_url
    return os.path.join(base_path, relative_path)

if any(platform.win32_ver()):
    from ctypes import windll

    windll.shcore.SetProcessDpiAwareness(1)

legacy_database_path = os.path.join(os.getcwd(), 'labtycoon.db')
if not DATABASE_PATH.exists() and os.path.exists(legacy_database_path):
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy_database_path, DATABASE_PATH)

if not DATABASE_PATH.exists() or not database_is_initialized():
    print('database not exists.. in ' + str(DATABASE_PATH))
    initialize_db()
else:
    ensure_db_schema()

sg.theme('SystemDefault')
sg.set_options(font=('Helvetica', 13))

_project_serializer = ProjectSerializer()


def create_project_dialog() -> tuple[str, str] | None:
    layout = [
        [sg.Text('Project Name *', size=(14, 1)), sg.Input(key='-PROJECT-NAME-', focus=True)],
        [sg.Text('Description', size=(14, 1)), sg.Multiline(key='-PROJECT-DESCRIPTION-', size=(42, 5))],
        [sg.Push(), sg.Button('Create', bind_return_key=True), sg.Button('Cancel')],
    ]
    dialog = sg.Window('New Lab Tycoon Project', layout, modal=True)
    try:
        while True:
            event, values = dialog.read()
            if event in (sg.WIN_CLOSED, 'Cancel'):
                return None
            if event == 'Create':
                name = str(values.get('-PROJECT-NAME-', '')).strip()
                if not name:
                    sg.popup_error('Project name is required.', title='New Project')
                    continue
                return name, str(values.get('-PROJECT-DESCRIPTION-', '')).strip()
    finally:
        dialog.close()


def dirty_project_choice(project_name: str) -> str:
    layout = [
        [sg.Text(f'Save changes to {project_name}?')],
        [
            sg.Button('Save', key='save'),
            sg.Button("Don't Save", key='discard'),
            sg.Button('Cancel', key='cancel'),
        ],
    ]
    dialog = sg.Window('Unsaved Project', layout, modal=True)
    try:
        event, _values = dialog.read()
        return event if event in {'save', 'discard'} else 'cancel'
    finally:
        dialog.close()


def choose_project_path(*, save_as: bool) -> str | None:
    path = sg.popup_get_file(
        'Save Lab Tycoon Project' if save_as else 'Open Lab Tycoon Project',
        save_as=save_as,
        no_window=True,
        default_extension='.ltp' if save_as else None,
        file_types=(('Lab Tycoon Project', '*.ltp'),),
    )
    return str(path) if path else None


def get_token_and_decode_payload():
    current_token = keyring.get_password('labtycoon', 'access_token')
    if current_token:
        try:
            decoded_payload = jwt.decode(current_token, secret_key, algorithms=['HS256'])
        except jwt.exceptions.ExpiredSignatureError:
            pass
        else:
            return decoded_payload
    return None


def run_app():
    project_session.close()
    main_context = MainWindowContext(project_name='No project open', laboratory_name='')
    window = sg.Window('Lab Tycoon',
                       layout=build_main_layout(resource_path, main_context),
                       resizable=True,
                       element_justification='center').finalize()

    decoded_payload = get_token_and_decode_payload()
    if decoded_payload:
        session_manager.login(decoded_payload['username'])
        logger.info('USER %s SIGNED IN' % decoded_payload['username'])

    def toggle_buttons_after_log_in_out(action='login'):
        if action == 'login':
            window.find_element('-SIGNOUT-').update(visible=True)
            window.find_element('-SIGNIN-').update(visible=False)
        else:
            window.find_element('-SIGNOUT-').update(visible=False)
            window.find_element('-SIGNIN-').update(visible=True)

    if session_manager.current_user:
        toggle_buttons_after_log_in_out()

    def refresh_project_display() -> None:
        project = project_session.active_project
        if project is None:
            context = MainWindowContext(project_name='No project open', laboratory_name='')
            window.TKroot.title('Lab Tycoon')
        else:
            context = MainWindowContext(
                project_name=project.name,
                laboratory_name=project.name,
                modified=project.is_dirty,
                has_active_project=True,
            )
            window.TKroot.title(f'Lab Tycoon — {project.display_name}')
        update_context_elements(window, context)

    def save_active_project(*, force_path: bool = False) -> bool:
        project = project_session.active_project
        if project is None:
            sg.popup_error('No project is currently open.', title='Save Project', keep_on_top=True)
            return False
        destination = None if force_path else project.file_path
        if destination is None:
            selected_path = choose_project_path(save_as=True)
            if selected_path is None:
                return False
            destination = selected_path
        try:
            saved_path = _project_serializer.save(project, destination)
        except (OSError, ProjectFormatError, ValueError) as exc:
            sg.popup_error(f'Project could not be saved:\n{exc}', title='Save Project', keep_on_top=True)
            return False
        update_config_yaml(last_project_path=str(saved_path.resolve()))
        refresh_project_display()
        sg.popup_quick_message(f'Project saved to {saved_path}.', background_color='lightgreen',
                               keep_on_top=True)
        return True

    def may_replace_active_project() -> bool:
        project = project_session.active_project
        if project is None or not project.is_dirty:
            return True
        choice = dirty_project_choice(project.name)
        if choice == 'cancel':
            return False
        if choice == 'save':
            return save_active_project()
        return True

    remembered_project_path = config_dict.get('last_project_path')
    if remembered_project_path:
        try:
            remembered_project = _project_serializer.load(str(remembered_project_path))
        except ProjectFormatError as exc:
            update_config_yaml(last_project_path=None)
            sg.popup_error(
                f'The previously open project could not be restored:\n{exc}',
                title='Restore Project',
            )
        else:
            project_session.activate(remembered_project)

    refresh_project_display()

    while True:
        event, values = window.read()
        if event == sg.WINDOW_CLOSED or event == 'Exit':
            if may_replace_active_project():
                break
            continue
        elif event == 'Register':
            create_register_window()
        elif event == '-SIGNIN-' or event == 'Sign In':
            if not session_manager.current_user:
                create_signin_window()
                current_user = get_token_and_decode_payload()
                if current_user:
                    toggle_buttons_after_log_in_out()
                    sg.popup_quick_message(f"👍You have logged in.", background_color='lightgreen')
            else:
                sg.popup_quick_message('You are already logged in.')
        elif event == '-SIGNOUT-' or event == 'Sign Out':
            if session_manager.current_user:
                if keyring.get_password('labtycoon', 'access_token'):
                    keyring.delete_password('labtycoon', 'access_token')
                session_manager.logout()
                toggle_buttons_after_log_in_out('logout')
                sg.popup_quick_message("You have logged out.🙌", background_color='lightgreen')
            else:
                sg.popup_quick_message('You are already signed out.')
        elif event == 'Profile':
            if session_manager.current_user:
                create_profile_window()
            else:
                sg.popup_error('Please sign in to access this section.', title='Access Denied')
        elif event == 'About Lab Tycoon':
            sg.popup_ok('This program is developed by Asst. Prof.Likit Preeyanon. '
                        'Please contact likit.pre@mahidol.edu for more information.'
                        , title='About')
        elif event == 'Open Demo Project':
            if not may_replace_active_project():
                continue
            demo_project = LabTycoonProject.create('Demo Laboratory', 'Built-in demonstration laboratory.')
            demo_project.is_dirty = False
            project_session.activate(demo_project)
            update_config_yaml(last_project_path=None)
            refresh_project_display()
            sg.popup_quick_message('Demo Laboratory is ready.', background_color='lightgreen')
        elif event in ('New Project...', '-NEW-PROJECT-'):
            if not may_replace_active_project():
                continue
            project_values = create_project_dialog()
            if project_values is not None:
                project_session.activate(LabTycoonProject.create(*project_values))
                update_config_yaml(last_project_path=None)
                refresh_project_display()
        elif event in ('Open Project...', '-OPEN-PROJECT-'):
            if not may_replace_active_project():
                continue
            selected_path = choose_project_path(save_as=False)
            if selected_path is not None:
                try:
                    project_session.activate(_project_serializer.load(selected_path))
                except ProjectFormatError as exc:
                    sg.popup_error(f'Project could not be opened:\n{exc}', title='Open Project')
                else:
                    update_config_yaml(
                        last_project_path=str(project_session.active_project.file_path.resolve())
                    )
                    refresh_project_display()
                    create_project_workspace_window(project_session.active_project)
        elif event == 'Save':
            save_active_project()
        elif event == 'Save As...':
            save_active_project(force_path=True)
        elif event == 'Close Project':
            if project_session.active_project is not None and may_replace_active_project():
                project_session.close()
                update_config_yaml(last_project_path=None)
                refresh_project_display()
        elif event == '-GO-TO-LAB-':
            project = project_session.active_project
            if project is not None:
                create_project_workspace_window(project)
        elif event == 'SQL Editor':
            create_sql_window()
        elif event == 'Database Explorer':
            create_database_explorer_window()
        elif event == 'Settings...':
            if create_lis_settings_window() and may_replace_active_project():
                break
        elif event in ('Add Workflow...', 'Open Workflow...'):
            project = project_session.active_project
            if project is None:
                sg.popup_error('No project is currently open. Create or open a Lab Tycoon project first.')
            else:
                workflow_id = (
                    add_project_workflow(project) if event == 'Add Workflow...'
                    else choose_project_workflow(project)
                )
                if workflow_id is not None:
                    open_project_workflow(project, workflow_id)
                refresh_project_display()
        elif event == 'Rule Preview':
            create_rule_preview_window()
        elif event == 'Floor Plan' or event == '-FLOOR-VIEW-':
            project = project_session.active_project
            if project is None:
                sg.popup_error('No project is currently open. Create or open a Lab Tycoon project first.')
            else:
                create_floor_view_window(project)
                refresh_project_display()
        elif event == 'Analyze' or event == '-ANALYZE-':
            create_analysis_window()
        elif event == 'Orders' or event == '-ORDERS-':
            create_order_list_window()
        elif event == 'Logs' or event == '-LOGGING-':
            create_logging_window()
        elif event == 'Manage Users' or event == '-USERS-':
            create_user_list_window()
        elif event == 'Tests' or event == '-TESTS-':
            create_test_list_window()
        elif event == 'Customers' or event == '-CUSTOMER-':
            create_customer_list_window()

    project_session.close()
    window.close()
