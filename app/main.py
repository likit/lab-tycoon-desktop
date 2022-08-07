import PySimpleGUI as sg
import threading
import requests
from client.windows import *
from server.main import app

from server.models import *

with app.app_context():
    db.create_all()

sg.theme('DarkAmber')

layout = [
    [sg.Text('Lab Tycoon V.2022.1', font=('Arial', 34))],
    [sg.Text('A Demonstration Lab Information System for Education', font=('Arial', 20))],
    [sg.Text('By Faculty of Medial Technology, Mahidol University', font=('Arial', 16))],
    [sg.Text('โปรแกรมนี้พัฒนาสำหรับใช้ในการเรียนการสอนเท่านั้น ทางผู้พัฒนาไม่รับประกันความเสียหายที่อาจเกิดขึ้นหากนำไปใช้ในห้องปฏิบัติการจริง', font=('Arial', 14))],
    [sg.Button('Register', key='-REGISTER-'),
     sg.Button('Edit profile', key='-EDIT-PROFILE-', visible=False),
     sg.Button('Sign In', key='-SIGNIN-'),
     sg.Button('Sign Out', key='-SIGNOUT-', visible=False),
     sg.Button('Admin', key='-ADMIN-'),
     sg.Exit(button_color='white on red')]
]

window = sg.Window('Lab Tycoon Desktop!', layout=layout, element_justification='center').finalize()
flask_job = threading.Thread(target=lambda: app.run(use_reloader=False, debug=True))
flask_job.start()
access_token = ''

while True:
    event, values = window.read()
    if event == sg.WINDOW_CLOSED or event == 'Exit':
        print('Trying to terminate Flask app')
        requests.get('http://127.0.0.1:5000/kill')
        flask_job.join()
        break
    elif event == '-REGISTER-':
        create_register_window()
    elif event == '-SIGNIN-':
        access_token = create_singin_window()
        if access_token:
            window.find_element('-SIGNOUT-').update(visible=True)
            window.find_element('-EDIT-PROFILE-').update(visible=True)
            window.find_element('-SIGNIN-').update(visible=False)
    elif event == '-SIGNOUT-':
        if sg.popup_yes_no('You sure want to sign out?') == 'Yes':
            headers = {'Authorization': f'Bearer {access_token}'}
            resp = requests.get('http://127.0.0.1:5000/auth/sign-out', headers=headers)
            if resp.status_code == 200:
                sg.popup_auto_close('You have logged out.')
                window.find_element('-SIGNOUT-').update(visible=False)
                window.find_element('-SIGNIN-').update(visible=True)
                window.find_element('-EDIT-PROFILE-').update(visible=False)
            else:
                sg.popup_error(resp.json().get('message'))
    elif event == '-EDIT-PROFILE-':
        if access_token:
            create_profile_window(access_token)
        else:
            sg.popup_error('Access denied.')
    elif event == '-ADMIN-':
        if access_token:
            headers = {'Authorization': f'Bearer {access_token}'}
            resp = requests.get('http://127.0.0.1:5000/api/admin', headers=headers)
            print(resp.json())
        else:
            sg.popup_error('Access denied.')

window.close()
