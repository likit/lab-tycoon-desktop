import logging

import FreeSimpleGUI as sg
from datetime import datetime, timedelta, timezone
import jwt
import keyring

from app.config import secret_key

logger = logging.getLogger('client')


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
            payload = {
                'username': values['username'],
                'exp': expiration_datetime,
            }

            try:
                access_token = jwt.encode(payload, secret_key, algorithm='HS256')
            except Exception as e:
                sg.popup_error(f'{e}')
            else:
                logger.info('USER %s SIGNED IN' % values['username'])
                sg.popup_ok(f'Logged in as {values["username"]}')
                keyring.set_password('labtycoon', 'access_token', access_token)
                break
    window.close()
    return access_token
