import functools
import FreeSimpleGUI as sg
from datetime import datetime, timedelta

import bcrypt
import jwt
import keyring
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.config import secret_key, logger
from app.system.models import engine, User


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
        if not session_manager.is_logged_in() or not session_manager.current_user.active:
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
                        session_manager.login(user)
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
    user = session_manager.current_user
    layout = [
        [sg.Text('Username', size=(8, 1)), sg.InputText(user.username, disabled=True, key='username')],
        [sg.Text('First Name', size=(8, 1)), sg.InputText(user.firstname, key='firstname')],
        [sg.Text('Last Name', size=(8, 1)), sg.InputText(user.lastname, key='lastname')],
        [sg.Text('Email', size=(8, 1)), sg.InputText(user.email, key='email')],
        [sg.Text('Position', size=(8, 1)), sg.InputText(user.position, key='position')],
        [sg.Text('License ID', size=(8, 1)), sg.InputText(user.license_id, key='license_id')],
        [
            sg.Button('Change Password', key='-PASSWORD-', button_color=('white', 'red')),
            sg.Button('Submit'),
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
        else:
            # resp = requests.patch('http://127.0.0.1:5000/api/users', json=values, headers=headers)
            # message = resp.json().get('message')
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
