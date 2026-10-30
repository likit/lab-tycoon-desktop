from app.ui_styles import create_window, active_window
import os.path
import platform
import shutil
import sys

import FreeSimpleGUI as sg
import jwt
import keyring

from app.auth.windows import (create_initial_admin_window,
                              create_signin_window,
                              create_profile_window,
                              create_register_window,
                              create_user_list_window)
from app.system.models import database_is_initialized, ensure_db_schema, initialize_db
from app.system.floor_view import create_floor_view_window
from app.main_ui import MainWindowContext, build_main_layout, build_main_menu, update_context_elements, project_action_allowed
from app.program_settings import create_program_settings_window, simulation_menu_enabled
from app.lis_settings import create_lis_settings_window
from app.database_explorer import create_database_explorer_window
from app.project import LabTycoonProject, project_session
from app.demo_project import create_demo_project
from app.demo_setup import create_demo_setup_window, create_project_setup_window, show_demo_ready
from app.project_workspace import add_project_workflow, choose_project_workflow, create_project_workspace_window, open_project_workflow, open_project_lab, create_lis_results_window
from app.project_serializer import ProjectFormatError, ProjectSerializer
from app.recent_projects import recent_project_paths, recent_project_index, CLEAR_RECENT_PROJECTS
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


def create_project_dialog():
    return create_project_setup_window()


def dirty_project_choice(project_name: str) -> str:
    layout = [
        [sg.Text(f'Save changes to {project_name}?')],
        [
            sg.Button('Save', key='save'),
            sg.Button("Don't Save", key='discard'),
            sg.Button('Cancel', key='cancel'),
        ],
    ]
    dialog = create_window('Unsaved Project', layout, modal=True)
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
        except jwt.exceptions.InvalidTokenError:
            pass
        else:
            return decoded_payload
    return None


def run_app():
    pending_demo = config_dict.get('pending_demo')
    pending_project = config_dict.get('pending_project')
    if pending_demo or pending_project:
        session_manager.logout()
        if keyring.get_password('labtycoon', 'access_token'):
            keyring.delete_password('labtycoon', 'access_token')
    if not create_initial_admin_window():
        return
    project_session.close()
    main_context = MainWindowContext(project_name='No project open', laboratory_name='')
    window = create_window('Lab Tycoon',
                       independent=True,
                       layout=build_main_layout(resource_path, main_context, simulation_enabled=simulation_menu_enabled(),
                                                recent_projects=recent_project_paths(config_dict)),
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
            window.TKroot.title('Lab Tycoon — Open a project to continue')
        else:
            context = MainWindowContext(
                project_name=project.name,
                laboratory_name=project.name,
                modified=project.is_dirty,
                has_active_project=True,
            )
            window.TKroot.title(f'Lab Tycoon — {project.display_name}')
        update_context_elements(window, context, simulation_enabled=simulation_menu_enabled(),
                                recent_projects=recent_project_paths(config_dict))

    def remember_project(path):
        update_config_yaml(last_project_path=str(path.resolve()),
                           recent_project_paths=recent_project_paths(config_dict, latest=str(path)))

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
        remember_project(saved_path)
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

    if pending_demo:
        project_session.activate(create_demo_project(
            pending_demo['name'], analyzer_tests=pending_demo.get('analyzer_tests', ['TG'])))
        update_config_yaml(pending_demo=None, last_project_path=None)

    elif pending_project:
        project_session.activate(LabTycoonProject.create(**pending_project))
        update_config_yaml(pending_project=None, last_project_path=None)

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
            remember_project(remembered_project.file_path)

    from app.order_source_preferences import restore_order_source
    source_restore_error = restore_order_source()
    if source_restore_error:
        sg.popup_error(source_restore_error, title='Restore Order Source')

    refresh_project_display()

    while True:
        event, values = window.read()
        if event == sg.WINDOW_CLOSED or event == 'Exit':
            if may_replace_active_project():
                break
            continue
        elif not project_action_allowed(event, project_session.active_project is not None):
            sg.popup_error('Create or open a project before using the workspace.', title='Project Required')
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
            setup = create_demo_setup_window()
            if setup is None:
                continue
            demo_project, needs_restart = setup
            if needs_restart:
                sg.popup_ok('Database configured. Reopen Lab Tycoon to continue with your demo.\n'
                            'The demo will open automatically after administrator setup, if needed.',
                            title='Restart Required')
                break
            project_session.activate(demo_project)
            update_config_yaml(last_project_path=None)
            refresh_project_display()
            show_demo_ready(demo_project, parent=window)
        elif event in ('New Project...', '-NEW-PROJECT-'):
            if not may_replace_active_project():
                continue
            project_values = create_project_dialog()
            if project_values is not None:
                new_project, needs_restart = project_values
                if needs_restart:
                    sg.popup_ok('Database configured. Reopen Lab Tycoon to continue with your project.\n'
                                'Administrator setup will appear if needed.', title='Restart Required')
                    break
                project_session.activate(new_project)
                update_config_yaml(last_project_path=None)
                refresh_project_display()
        elif event == CLEAR_RECENT_PROJECTS:
            update_config_yaml(recent_project_paths=[])
            refresh_project_display()
        elif event in ('Open Project...', '-OPEN-PROJECT-') or recent_project_index(event) is not None:
            recent_index = recent_project_index(event)
            # Resolve the menu selection before a possible Save changes history.
            paths = recent_project_paths(config_dict)
            selected_path = paths[recent_index] if recent_index is not None and recent_index < len(paths) else None
            if not may_replace_active_project():
                continue
            if recent_index is None:
                selected_path = choose_project_path(save_as=False)
            if selected_path is not None:
                try:
                    project_session.activate(_project_serializer.load(selected_path))
                except ProjectFormatError as exc:
                    sg.popup_error(f'Project could not be opened:\n{exc}', title='Open Project')
                    if recent_index is not None and not os.path.isfile(selected_path):
                        update_config_yaml(recent_project_paths=[path for path in recent_project_paths(config_dict)
                                                                if path != selected_path])
                        refresh_project_display()
                else:
                    remember_project(project_session.active_project.file_path)
                    refresh_project_display()
                    open_project_lab(project_session.active_project, home_window=window, on_save=save_active_project)
                    refresh_project_display()
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
                open_project_lab(project, home_window=window, on_save=save_active_project)
                refresh_project_display()
        elif event == 'SQL Editor':
            create_sql_window()
        elif event == 'Database Explorer':
            create_database_explorer_window()
        elif event == 'Program Settings...':
            if create_program_settings_window(parent=window):
                refresh_project_display()
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
        elif event == 'Scenarios':
            from app.project_workspace import open_project_scenarios
            project = project_session.active_project
            if project is None:
                sg.popup_error('Open a project before managing scenarios.')
            else:
                scenario_parent = active_window(role='floor') or active_window() or window
                workflow_id = choose_project_workflow(project, parent=scenario_parent)
                if workflow_id is not None:
                    open_project_scenarios(project, workflow_id, parent=scenario_parent)
                    refresh_project_display()
        elif event == 'Rule Preview':
            create_rule_preview_window()
        elif event == 'Floor Plan' or event == '-FLOOR-VIEW-':
            project = project_session.active_project
            if project is None:
                sg.popup_error('No project is currently open. Create or open a Lab Tycoon project first.')
            else:
                open_project_lab(project, home_window=window, on_save=save_active_project)
                refresh_project_display()
        elif event == 'Analyze' or event == '-ANALYZE-':
            create_analysis_window()
        elif event == 'Orders' or event == '-ORDERS-':
            create_order_list_window()
        elif event in ('Results', '-RESULTS-'):
            create_lis_results_window()
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
