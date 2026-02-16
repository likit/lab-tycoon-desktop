import os.path
import platform
import FreeSimpleGUI as sg
import jwt
import keyring

from app.auth.windows import create_signin_window
from app.server.models import initialize_db
from app.config import secret_key

BASE_URL = os.getcwd()

if any(platform.win32_ver()):
    from ctypes import windll

    windll.shcore.SetProcessDpiAwareness(1)

base_url = os.path.dirname(os.path.abspath(__file__))

if not os.path.exists(os.path.join(base_url, 'instance', 'app.db')):
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
    [sg.Text('Lab Tycoon V.2022.1', font=('Arial', 34))],
    [sg.Text('A Demonstration Lab Information System for Education', font=('Arial', 20))],
    [sg.Text('By Faculty of Medial Technology, Mahidol University', font=('Arial', 16))],
    [sg.Text('โปรแกรมนี้พัฒนาสำหรับใช้ในการเรียนการสอนเท่านั้น '
             'ทางผู้พัฒนาไม่รับประกันความเสียหายที่อาจเกิดขึ้นหากนำไปใช้ในห้องปฏิบัติการจริง', font=('Arial', 14))],
    [sg.Button('Edit profile', key='-EDIT-PROFILE-', visible=True),
     sg.Button('Sign In', key='-SIGNIN-'),
     sg.Button('Sign Out', key='-SIGNOUT-', visible=False),
     sg.Button('Analyze', key='-ANALYZE-'),
     sg.Button('Order List', key='-order-list-'),
     sg.Button('Patient', key='-PATIENT-'),
     sg.Button('Logs', key='-LOGGING-'),
     sg.Exit(button_color='white on red')]
]

window = sg.Window('Lab Tycoon Desktop!',
                   layout=layout,
                   element_justification='center').finalize()


def get_token_and_decode_payload():
    current_token = keyring.get_password('labtycoon', 'access_token')
    current_user = None
    if current_token:
        try:
            decoded_payload = jwt.decode(current_token.encode('utf-8'), secret_key, algorithms=['HS256'])
        except jwt.exceptions.ExpiredSignatureError:
            pass
        else:
            current_user = decoded_payload['username']

    return current_user

def toggle_buttons_after_log_in_out(action='login'):
    if action == 'login':
        window.find_element('-SIGNOUT-').update(visible=True)
        window.find_element('-EDIT-PROFILE-').update(visible=True)
        window.find_element('-SIGNIN-').update(visible=False)
    else:
        window.find_element('-SIGNOUT-').update(visible=False)
        window.find_element('-EDIT-PROFILE-').update(visible=False)
        window.find_element('-SIGNIN-').update(visible=True)

current_user = get_token_and_decode_payload()
if current_user:
    toggle_buttons_after_log_in_out()


while True:
    event, values = window.read()
    if event == sg.WINDOW_CLOSED or event == 'Exit':
        break
    # elif event == 'Register':
    #     if not access_token:
    #         sg.popup_error('Please sing in to access this section.')
    #     else:
    #         # create_register_window()
    #         print('foo')
    elif event == '-SIGNIN-':
        if not current_user:
            access_token = create_signin_window()
            current_user = get_token_and_decode_payload()
            if current_user:
                toggle_buttons_after_log_in_out()
    elif event == '-SIGNOUT-':
        keyring.delete_password('labtycoon', 'access_token')
        current_user = None
        toggle_buttons_after_log_in_out('logout')
        sg.popup_auto_close('You have logged out.')
    # elif event == '-EDIT-PROFILE-':
    #     if access_token:
    #         create_profile_window(access_token)
    #     else:
    #         sg.popup_error('Please sign in to access this section.', title='Access Denied')
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
    # elif event == '-LOGGING-':
    #     if not access_token:
    #         sg.popup_error('Please sign in to access this section.', title='Access Denied')
    #     else:
    #         create_logging_window(access_token)
    # elif event == 'Manage':
    #     create_user_list_window(access_token)
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
