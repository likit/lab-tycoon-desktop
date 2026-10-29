"""Configure the built-in demo and safely stage database changes for restart."""
import FreeSimpleGUI as sg
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app.config import DATABASE_CONFIG, update_config_yaml
from app.database import DatabaseConfig, DatabaseManager
from app.demo_project import create_demo_project
from app.system.models import initialize_db
from app.ui_styles import create_window


def prepare_demo(name, database_path, *, create_new=False):
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
        finally:
            target_engine.dispose()
    else:
        manager.test_connection()
        target_engine = manager.create_orm_engine()
        try:
            inspector = inspect(target_engine)
            if not all(inspector.has_table(table) for table in ('users', 'lab_orders', 'tests')):
                raise ValueError('Select an existing Lab Tycoon database, or create a new one.')
        finally:
            target_engine.dispose()
    project = create_demo_project(name)
    changed = config != DATABASE_CONFIG.normalized()
    if changed:
        update_config_yaml(database=config.to_dict(), last_project_path=None,
                           pending_demo={'name': name, 'database_path': config.database_path},
                           order_source_selection=None)
    return project, changed


def create_demo_setup_window():
    layout = [
        [sg.Text('Project name'), sg.Input('Demo Laboratory', key='name')],
        [sg.Radio('Use existing database', 'database', default=True, key='existing', enable_events=True),
         sg.Radio('Create new database', 'database', key='new', enable_events=True)],
        [sg.Text('Database file'), sg.Input(DATABASE_CONFIG.database_path, key='path', expand_x=True),
         sg.Button('Browse…')],
        [sg.Text('Changing the database applies to the whole app and requires reopening it.\n'
                 'Your named demo opens after restart. A new database requires administrator setup.')],
        [sg.Button('Open Demo'), sg.Button('Cancel')],
    ]
    window = create_window('Open Demo Project', layout, modal=True, resizable=True)
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
            elif event == 'Open Demo':
                try:
                    return prepare_demo(values['name'], values['path'], create_new=bool(values['new']))
                except (ValueError, OSError, SQLAlchemyError) as exc:
                    sg.popup_error(str(exc), title='Demo Setup')
    finally:
        window.close()
