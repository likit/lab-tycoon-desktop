import functools
import FreeSimpleGUI as sg
from datetime import datetime, timedelta

import bcrypt
import jwt
import keyring
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.config import secret_key, logger
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
        # --- YOUR CHECK LOGIC GOES HERE ---
        # For this example, we'll check a mock global variable.
        # In a real app, you might check 'session.get("user")' or similar.
        with Session(engine) as session:
            query = select(User).where(User.username == session_manager.current_user)
            user = session.scalar(query)
        if not session_manager.is_logged_in() or not user.active:
            sg.popup_error("⛔ Access Denied: You must be logged in or activate your account.")
            return None  # Or redirect, or raise error

        # If check passes, execute the original function
        return func(*args, **kwargs)

    return wrapper

def create_signin_window():
    layout = [
        [sg.Text('Username', size=(8, 1)), sg.InputText(focus=True, key='username')],
        [sg.Text('Password', size=(8, 1)), sg.Input(password_char='*', key='password')],
        [sg.Button('Submit'), sg.Exit()],
    ]

    window = sg.Window('Sign In', layout=layout, modal=True)
    access_token = ''
    expiration_datetime = datetime.now() - timedelta(hours=1)

    while True:
        event, values = window.read()
        if event in ['Exit', sg.WIN_CLOSED]:
            break
        else:
            session = Session(engine)
            query = select(User).where(User.username == values['username'])
            user = session.scalars(query).one()
            if user:
                if not user.active:
                    logger.info(f'USER {values["username"]} ATTEMPTED TO SIGN IN WITH INACTIVE ACCOUNT.')
                    sg.popup_ok(f'The account with username {user.username} has been deactivated.')
                    break
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
                        sg.popup_ok(f'Logged in as {values["username"]}')
                        break
                else:
                    logger.info(f'USER {values["username"]} ATTEMPTED TO SIGN IN WITH INVALID PASSWORD.')
                    sg.popup_error('Invalid password.')
            else:
                logger.info(f'USER {values["username"]} ATTEMPTED TO SIGN IN WITH INVALID USERNAME.')
                sg.popup_error('Invalid username.')
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
                sg.CloseButton('Close')
             ],
        ]

        window = sg.Window('User Profile', layout=layout, modal=True)

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
        [sg.Button('Submit'), sg.CloseButton('Close')]
    ]
    window = sg.Window('User Profile', layout=layout, modal=True)

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
        [sg.Button('Submit'), sg.Exit()],
    ]

    window = sg.Window('Register', layout=layout, modal=True)

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
            [sg.Exit('Close')]
        ]

        window = sg.Window('Users', layout=layout, resizable=True, modal=True, finalize=True)
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
                [sg.Button('Update'), sg.Button('Close')]
            )
        window = sg.Window('User Roles', layout, modal=True)
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

