import functools
import FreeSimpleGUI as sg
from datetime import datetime, timedelta

import bcrypt
import jwt
import keyring
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.config import secret_key, logger
from app.ui_styles import create_window, DANGER_BUTTON_COLOR
from app.system.models import engine, User, UserRole


class SessionManager:
    _instance = None

    def __new__(cls):
        """Standard Python Singleton implementation"""
        if cls._instance is None:
            cls._instance = super(SessionManager, cls).__new__(cls)
            cls._instance.current_user = None  # The actual data
        return cls._instance

    def login(self, user_data):
        self.current_user = user_data
        print(f"🔓 Logged in as: {self.current_user}")

    def logout(self):
        self.current_user = None
        print("🔒 Logged out")

    def is_logged_in(self):
        return self.current_user is not None


session_manager = SessionManager()

def login_required(func):
    """
    Decorator that checks if a user is logged in.
    """

    @functools.wraps(func)  # Preserves the metadata of the original function
    def wrapper(*args, **kwargs):
        if not session_manager.is_logged_in():
            sg.popup_error("⛔ Access Denied: You must be logged in or activate your account.")
            return None
        with Session(engine) as session:
            query = select(User).where(User.username == session_manager.current_user)
            user = session.scalar(query)
        if user is None or not user.active:
            sg.popup_error("⛔ Access Denied: You must be logged in or activate your account.")
            return None

        # If check passes, execute the original function
        return func(*args, **kwargs)

    return wrapper

def users_table_is_empty(database_engine=None):
    with Session(database_engine or engine) as session:
        return session.scalar(select(User.id).limit(1)) is None


def create_initial_admin(values, database_engine=None):
    """Create the first account only, with every available role."""
    fields = {name: str(values.get(name, '')).strip()
              for name in ('username', 'firstname', 'lastname', 'email', 'position', 'license_id')}
    if not all(fields[name] for name in ('username', 'firstname', 'lastname')):
        raise ValueError('Username, first name, and last name are required.')
    password = values.get('password', '')
    if not password or not password.strip():
        raise ValueError('Password is required.')
    if password != values.get('confirm_password', ''):
        raise ValueError('Passwords do not match.')
    if len(password.encode('utf-8')) > 72:
        raise ValueError('Password must be at most 72 UTF-8 bytes.')
    with Session(database_engine or engine) as session:
        if session.scalar(select(User.id).limit(1)) is not None:
            raise ValueError('An account already exists. Sign in to manage users.')
        roles = list(session.scalars(select(UserRole)))
        for name in ('admin', 'approver', 'reporter'):
            if not any(role.role_need == name for role in roles):
                roles.append(UserRole(role_need=name))
        fields['email'] = fields['email'] or None
        user = User(**fields, active=True, roles=roles)
        user.password = password
        session.add(user)
        session.commit()


def create_initial_admin_window():
    if not users_table_is_empty():
        return True
    layout = [[sg.Text('Create your administrator account. This account receives all roles.')]]
    for label, key in (('First Name *', 'firstname'), ('Last Name *', 'lastname'),
                       ('Email', 'email'), ('Position', 'position'), ('License ID', 'license_id'),
                       ('Username *', 'username'), ('Password *', 'password'),
                       ('Confirm Password *', 'confirm_password')):
        layout.append([sg.Text(label, size=(18, 1)),
                       sg.Input(key=key, password_char='*' if 'password' in key else '',
                                focus=key == 'firstname')])
    layout.append([sg.Button('Create Account'), sg.Button('Exit', button_color=DANGER_BUTTON_COLOR)])
    window = create_window('Set Up Administrator', layout, modal=True)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, 'Exit'):
                return False
            if event == 'Create Account':
                try:
                    create_initial_admin(values)
                except (ValueError, SQLAlchemyError) as exc:
                    sg.popup_error(str(exc), title='Administrator Setup')
                    continue
                return True
    finally:
        window.close()


def create_signin_window():
    layout = [
        [sg.Text('Username', size=(8, 1)), sg.InputText(focus=True, key='username')],
        [sg.Text('Password', size=(8, 1)), sg.Input(password_char='*', key='password')],
        [sg.Button('Submit'), sg.Exit(button_color=DANGER_BUTTON_COLOR)],
    ]

    window = create_window('Sign In', layout=layout, modal=True)
    access_token = ''
    expiration_datetime = datetime.now() - timedelta(hours=1)

    while True:
        event, values = window.read()
        if event in ['Exit', sg.WIN_CLOSED]:
            break
        else:
            session = Session(engine)
            query = select(User).where(User.username == values['username'])
            user = session.scalar(query)
            if user:
                if not user.active:
                    logger.info(f'USER {values["username"]} ATTEMPTED TO SIGN IN WITH INACTIVE ACCOUNT.')
                    sg.popup_quick_message(f'😢 The account with username {user.username} has been deactivated.',
                                    icon=sg.SYSTEM_TRAY_MESSAGE_ICON_CRITICAL)
                if bcrypt.checkpw(values['password'].encode('utf-8'), user.hashed_password):
                    payload = {
                        'username': values['username'],
                        'exp': expiration_datetime,
                    }
                    try:
                        access_token = jwt.encode(payload, secret_key, algorithm='HS256')
                    except Exception as e:
                        sg.popup_error(f'{e}')
                    else:
                        session_manager.login(user.username)
                        logger.info('USER %s SIGNED IN' % values['username'])
                        keyring.set_password('labtycoon', 'access_token', access_token)
                        break
                else:
                    logger.info(f'USER {values["username"]} ATTEMPTED TO SIGN IN WITH INVALID PASSWORD.')
                    sg.popup_quick_message(f"⛔ Invalid password", background_color='lightgreen')
            else:
                logger.info(f'USER {values["username"]} ATTEMPTED TO SIGN IN WITH INVALID USERNAME.')
                sg.popup_quick_message(f"⛔ Invalid username", background_color='lightgreen')
    window.close()
    return access_token


@login_required
def create_profile_window():
    with Session(engine) as session:
        query = select(User).where(User.username == session_manager.current_user)
        user = session.scalar(query)
        layout = [
            [sg.Text('Username', size=(8, 1)), sg.InputText(user.username, disabled=True, key='username')],
            [sg.Text('First Name', size=(8, 1)), sg.InputText(user.firstname, key='firstname')],
            [sg.Text('Last Name', size=(8, 1)), sg.InputText(user.lastname, key='lastname')],
            [sg.Text('Email', size=(8, 1)), sg.InputText(user.email, key='email')],
            [sg.Text('Position', size=(8, 1)), sg.InputText(user.position, key='position')],
            [sg.Text('License ID', size=(8, 1)), sg.InputText(user.license_id, key='license_id')],
            [
                sg.Button('Change Password', key='-PASSWORD-', button_color=('white', 'red')),
                sg.Button('Save'),
                sg.CloseButton('Close', button_color=DANGER_BUTTON_COLOR)
             ],
        ]

        window = create_window('User Profile', layout=layout, modal=True)

        while True:
            event, values = window.read()
            if event in ['Exit', sg.WIN_CLOSED, 'Close']:
                break
            elif event == '-PASSWORD-':
                create_password_setting_window()
            elif event == 'Save':
                user.firstname = values['firstname']
                user.lastname = values['lastname']
                user.email = values['email']
                user.position = values['position']
                user.license_id = values['license_id']
                session.add(user)
                session.commit()
                sg.popup_ok(f'Data have been saved.')
                break
    window.close()


@login_required
def create_password_setting_window():
    layout = [
        [sg.Text('Old Password', size=(15, 1)), sg.InputText(key='-OLD-PWD-', password_char='*')],
        [sg.Text('New Password', size=(15, 1)), sg.InputText(key='-NEW-PWD-', password_char='*')],
        [sg.Text('Confirm Password', size=(15, 1)), sg.InputText(key='-CF-PWD-', password_char='*')],
        [sg.Button('Submit'), sg.CloseButton('Close', button_color=DANGER_BUTTON_COLOR)]
    ]
    window = create_window('User Profile', layout=layout, modal=True)

    while True:
        event, values = window.read()
        if event in ['Exit', sg.WIN_CLOSED]:
            break
        elif event == 'Submit':
            session = Session(engine)
            query = select(User).where(User.username == session_manager.current_user)
            user = session.scalars(query).one()
            if values['-OLD-PWD-']:
                if bcrypt.checkpw(values['-OLD-PWD-'].encode('utf-8'), user.hashed_password):
                    if values['-NEW-PWD-'] == values['-CF-PWD-']:
                        user.password = values['-NEW-PWD-']
                        session.add(user)
                        session.commit()
                        break
                    else:
                        sg.popup_error('Passwords do not match.', title='Password Error')
                else:
                    sg.popup_error('Incorrect old passwords.', title='Password Error')
            else:
                sg.popup_error('Old password is required', title='Password Error')
    window.close()

@login_required
def create_register_window():
    layout = [
        [sg.Text('First Name', size=(8, 1)), sg.InputText(key='firstname')],
        [sg.Text('Last Name', size=(8, 1)), sg.InputText(key='lastname')],
        [sg.Text('Email', size=(8, 1)), sg.InputText(key='email')],
        [sg.Text('Position', size=(8, 1)), sg.InputText(key='position')],
        [sg.Text('License ID', size=(8, 1)), sg.InputText(key='license_id')],
        [sg.Text('Username', size=(8, 1)), sg.InputText(key='username')],
        [sg.Text('Password', size=(8, 1)), sg.Input(password_char='*', key='password')],
        [sg.Button('Submit'), sg.Exit(button_color=DANGER_BUTTON_COLOR)],
    ]

    window = create_window('Register', layout=layout, modal=True)

    while True:
        event, values = window.read()
        if event in ['Exit', sg.WIN_CLOSED]:
            break
        else:
            query = select(User).where(User.username == values['username'])
            with Session(engine) as session:
                _user = session.scalars(query).first()
                if _user:
                    sg.popup_error(f'Username {values["username"]} or email {values['email']} is already registered.')
                else:
                    user = User(firstname=values['firstname'],
                                lastname=values['lastname'],
                                email=values['email'],
                                position=values['position'],
                                license_id=values['license_id'],
                                username=values['username'])
                    user.password = values['password']
                    session.add(user)
                    session.commit()
                    sg.popup_ok('New username has been registered.')
                    break
    window.close()

@login_required
def create_user_list_window():
    with Session(engine) as session:
        query = select(User)
        data = []
        for user in session.scalars(query):
            data.append([
                user.firstname, user.lastname, user.license_id, user.username, user.position,
                user.all_roles, user.active
            ])
        layout = [
            [sg.Table(headings=['First', 'Last', 'License ID', 'Username', 'Position', 'Roles', 'Active'],
                      values=data, key='-TABLE-', enable_events=True)],
            [sg.Exit('Close', button_color=DANGER_BUTTON_COLOR)]
        ]

        window = create_window('Users', layout=layout, resizable=True, modal=True, finalize=True)
        window['-TABLE-'].bind("<Double-Button-1>", " Double")

        while True:
            event, values = window.read()
            if event in ['Close', sg.WIN_CLOSED]:
                break
            elif event == '-TABLE- Double' and values['-TABLE-']:
                username = data[values['-TABLE-'][0]][3]
                updated = create_admin_user_role_window(username)
                if updated:
                    data[values['-TABLE-'][0]][5] = updated['roles']
                    data[values['-TABLE-'][0]][6] = updated['-ACTIVE-']
                window.find_element('-TABLE-').update(values=data)
                window.refresh()
        window.close()

@login_required
def create_admin_user_role_window(username):
    with Session(engine) as session:
        query = select(User).where(User.username == username)
        user = session.scalar(query)
        user_roles = [role.role_need for role in user.roles]
        if user:
            fullname = user.firstname + ' ' + user.lastname
            layout = [
                [sg.Text('Username:'), sg.Text(username),
                 sg.Text('Name:'), sg.Text(fullname)],
                [sg.Text('License ID:'), sg.Text(user.license_id),
                 sg.Text('Position:'), sg.Text(user.position)],
                [sg.Checkbox('Active',
                             default=user.active,
                             key='-ACTIVE-',
                             enable_events=True)],
                [sg.Text('Role:')],
            ]

            query = select(UserRole)
            roles = [role for role in session.scalars(query)]
            for role in roles:
                if role.role_need in user_roles:
                    layout.append([
                        sg.Checkbox(role.role_need.title(), key=role.role_need, default=True, enable_events=True)
                    ])
                else:
                    layout.append([
                        sg.Checkbox(role.role_need.title(), key=role.role_need, default=False, enable_events=True)
                    ])

            layout.append(
                [sg.Button('Update'), sg.Button('Close', button_color=DANGER_BUTTON_COLOR)]
            )
        window = create_window('User Roles', layout, modal=True)
        while True:
            event, values = window.read()
            if event in ['Close', sg.WIN_CLOSED]:
                updates = None
                break
            elif event == 'Update':
                user.roles = []
                for role in roles:
                    if values[role.role_need] == True:
                        user.roles.append(role)
                user.active = values['-ACTIVE-']
                session.add(user)
                session.commit()
                sg.popup_ok("User has been updated.")
                user_roles = user.all_roles
                updates = {'roles': user_roles, '-ACTIVE-': values['-ACTIVE-']}
                break
        window.close()
    return updates
