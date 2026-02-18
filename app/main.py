import os.path
import platform
import FreeSimpleGUI as sg
import jwt
import keyring
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.windows import (create_signin_window,
                              create_profile_window,
                              create_register_window,
                              create_user_list_window)
from app.system.models import initialize_db, User, engine
from app.config import secret_key, logger
from app.auth.windows import SessionManager
from app.system.windows import create_logging_window

session_manager = SessionManager()

BASE_URL = os.getcwd()

if any(platform.win32_ver()):
    from ctypes import windll

    windll.shcore.SetProcessDpiAwareness(1)

base_url = os.path.dirname(os.path.abspath(__file__))

if not os.path.exists(os.path.join(base_url, 'labtycoon.db')):
    print('database not exists.. in ' + os.path.join(base_url, 'app', 'labtycoon.db'))
    initialize_db()

sg.theme('SystemDefault')
sg.set_options(font=('Helvetica', 12))
menu_def = [
    ['Users', ['Register', 'Manage']],
    ['Tests', ['List', 'Add TMLT test']],
    ['Tools', ['SQL Editor']],
    ['About', ['Program']],
]

layout = [
    [sg.Menu(menu_def)],
    [sg.Text('Lab Tycoon V.2029.1', font=('Arial', 34))],
    [sg.Text('A Demonstration Lab Information System for Education', font=('Arial', 20))],
    [sg.Text('By Likit Preeyanon, Ph.D.', font=('Arial', 16))],
    [sg.Text('Faculty of Medial Technology, Mahidol University', font=('Arial', 16))],
    [sg.Text('โปรแกรมนี้พัฒนาสำหรับใช้ในการเรียนการสอนเท่านั้น '
             'ทางผู้พัฒนาไม่รับประกันความเสียหายที่อาจเกิดขึ้นหากนำไปใช้ในห้องปฏิบัติการจริง', font=('Arial', 14))],
    [sg.Button('Edit profile', key='-EDIT-PROFILE-', visible=False),
     sg.Button('Sign In', key='-SIGNIN-'),
     sg.Button('Sign Out', key='-SIGNOUT-', visible=False),
     sg.Button('Analyze', key='-ANALYZE-'),
     sg.Button('Order List', key='-order-list-'),
     sg.Button('Patient', key='-PATIENT-'),
     sg.Button('Logs', key='-LOGGING-'),
     sg.Exit(button_color='white on red')]
]


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
    window = sg.Window('Lab Tycoon Desktop!',
                       layout=layout,
                       element_justification='center').finalize()

    decoded_payload = get_token_and_decode_payload()
    if decoded_payload:
        session_manager.login(decoded_payload['username'])
        logger.info('USER %s SIGNED IN' % decoded_payload['username'])

    def toggle_buttons_after_log_in_out(action='login'):
        if action == 'login':
            window.find_element('-SIGNOUT-').update(visible=True)
            window.find_element('-EDIT-PROFILE-').update(visible=True)
            window.find_element('-SIGNIN-').update(visible=False)
        else:
            window.find_element('-SIGNOUT-').update(visible=False)
            window.find_element('-EDIT-PROFILE-').update(visible=False)
            window.find_element('-SIGNIN-').update(visible=True)

    if session_manager.current_user:
        toggle_buttons_after_log_in_out()

    while True:
        event, values = window.read()
        if event == sg.WINDOW_CLOSED or event == 'Exit':
            break
        elif event == 'Register':
            create_register_window()
        elif event == '-SIGNIN-':
            if not session_manager.current_user:
                create_signin_window()
                current_user = get_token_and_decode_payload()
                if current_user:
                    toggle_buttons_after_log_in_out()
            else:
                sg.popup_error('You are already logged in.', title='User Authentication')
        elif event == '-SIGNOUT-':
            keyring.delete_password('labtycoon', 'access_token')
            session_manager.logout()
            toggle_buttons_after_log_in_out('logout')
            sg.popup_auto_close('You have logged out.')
        elif event == '-EDIT-PROFILE-':
            if session_manager.current_user:
                create_profile_window()
            else:
                sg.popup_error('Please sign in to access this section.', title='Access Denied')
        # elif event == 'Program':
        #     sg.popup_ok('This program is developed by Asst. Prof.Likit Preeyanon. '
        #                 'Please contact likit.pre@mahidol.edu for more information.'
        #                 , title='About')
        # elif event == 'SQL Editor':
        #     if not access_token:
        #         sg.popup_error('Please sign in to access this section.', title='Access Denied')
        #     else:
        #         create_sql_window()
        # elif event == '-ANALYZE-':
        #     create_analysis_window(access_token)
        # elif event == '-order-list-':
        #     if not access_token:
        #         sg.popup_error('Please sign in to access this section.', title='Access Denied')
        #     else:
        #         create_order_list_window(access_token)
        elif event == '-LOGGING-':
            create_logging_window()
        elif event == 'Manage':
            create_user_list_window()
        # elif event == 'BioSource':
        #     create_biosource_window(access_token)
        # elif event == 'Add TMLT test':
        #     if not access_token:
        #         sg.popup_error('Please sign in to access this section.')
        #     else:
        #         create_tmlt_test_window(access_token)
        # elif event == 'List':
        #     if not access_token:
        #         sg.popup_error('Please sign in to access this section.')
        #     else:
        #         create_test_list_window(access_token)
        # elif event == '-PATIENT-':
        #     if not access_token:
        #         sg.popup_error('Please sign in to access this section.')
        #     else:
        #         create_customer_list_window(access_token)


    window.close()
