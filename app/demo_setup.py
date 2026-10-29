"""Configure the built-in demo and safely stage database changes for restart."""
import FreeSimpleGUI as sg
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import DATABASE_CONFIG, update_config_yaml
from app.database import DatabaseConfig, DatabaseManager
from app.demo_project import create_demo_project
from app.project import LabTycoonProject
from app.system.models import initialize_db, Test
from app.ui_styles import create_window


def show_demo_ready(project, *, parent):
    """Use the shared owned-window lifecycle rather than an unmanaged popup."""
    layout = [[sg.Text(f'{project.name} is ready.', font=('Arial', 14, 'bold'))],
              [sg.Text('Open Simulation → Laboratory Design → Floor Plan, then Play.\n'
                       'The demo runs 10 orders with Alex. Save the project to keep your changes.')],
              [sg.Button('OK', bind_return_key=True)]]
    dialog = create_window('Demo Laboratory', layout, parent=parent, modal=True)
    try:
        while True:
            event, _ = dialog.read()
            if event in ('OK', sg.WIN_CLOSED):
                return
    finally:
        dialog.close()


def prepare_demo(name, database_path, *, create_new=False):
    return prepare_project(name, database_path, create_new=create_new, demo=True)


def prepare_project(name, database_path, *, create_new=False, demo=False, description=''):
    """Return (project, needs_restart); never replace an existing database file."""
    name = str(name).strip()
    if not name:
        raise ValueError('Project name is required.')
    if not str(database_path).strip():
        raise ValueError('Choose a database file.')
    config = DatabaseConfig.default_sqlite(database_path)
    manager = DatabaseManager(config)
    if create_new:
        manager.create_database()
        target_engine = manager.create_orm_engine()
        try:
            initialize_db(target_engine)
            analyzer_tests = _active_test_codes(target_engine)
        finally:
            target_engine.dispose()
    else:
        manager.test_connection()
        target_engine = manager.create_orm_engine()
        try:
            inspector = inspect(target_engine)
            if not all(inspector.has_table(table) for table in ('users', 'lab_orders', 'tests')):
                raise ValueError('Select an existing Lab Tycoon database, or create a new one.')
            analyzer_tests = _active_test_codes(target_engine)
        finally:
            target_engine.dispose()
    project = create_demo_project(name, analyzer_tests=analyzer_tests) if demo else LabTycoonProject.create(name, description)
    changed = config != DATABASE_CONFIG.normalized()
    if changed:
        update_config_yaml(database=config.to_dict(), last_project_path=None,
                           pending_demo=({'name': name, 'database_path': config.database_path,
                                          'analyzer_tests': analyzer_tests} if demo else None),
                           pending_project=(None if demo else {'name': name, 'description': project.description}),
                           order_source_selection=None)
    return project, changed


def _active_test_codes(database_engine):
    from sqlalchemy.orm import Session
    with Session(database_engine) as session:
        return sorted(session.scalars(select(Test.code).where(Test.active.is_(True))))


def create_demo_setup_window():
    return create_project_setup_window(demo=True)


def create_project_setup_window(*, demo=False):
    title = 'Open Demo Project' if demo else 'New Lab Tycoon Project'
    submit = 'Open Demo' if demo else 'Create'
    layout = [
        [sg.Text('Project name'), sg.Input('Demo Laboratory' if demo else '', key='name', focus=True)],
        *([] if demo else [[sg.Text('Description'), sg.Multiline(key='description', size=(42, 4))]]),
        [sg.Radio('Use existing database', 'database', default=True, key='existing', enable_events=True),
         sg.Radio('Create new database', 'database', key='new', enable_events=True)],
        [sg.Text('Database file'), sg.Input(DATABASE_CONFIG.database_path, key='path', expand_x=True),
         sg.Button('Browse…')],
        [sg.Text('Changing the database applies to the whole app and requires reopening it.\n'
                 'Your project opens after restart. A new database requires administrator setup.')],
        [sg.Button(submit), sg.Button('Cancel')],
    ]
    window = create_window(title, layout, modal=True, resizable=True)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, 'Cancel'):
                return None
            if event == 'new':
                window['path'].update('')
            elif event == 'existing':
                window['path'].update(DATABASE_CONFIG.database_path)
            elif event == 'Browse…':
                path = sg.popup_get_file('Choose SQLite Database', no_window=True,
                    save_as=bool(values['new']), default_extension='.db',
                    file_types=(('SQLite Database', '*.db *.sqlite *.sqlite3'),))
                if path:
                    window['path'].update(path)
            elif event == submit:
                try:
                    return (prepare_demo(values['name'], values['path'], create_new=bool(values['new']))
                            if demo else prepare_project(values['name'], values['path'],
                                create_new=bool(values['new']), description=values.get('description', '')))
                except (ValueError, OSError, SQLAlchemyError) as exc:
                    sg.popup_error(str(exc), title=title)
    finally:
        window.close()
