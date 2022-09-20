import os.path
import random

BASE_URL = os.getcwd()

import threading
from faker import Faker
from client.windows import *
from server.main import app
from server.models import *

fake = Faker()

base_url = os.path.dirname(os.path.abspath(__file__))

with app.app_context():
    if not os.path.exists(os.path.join(base_url, 'server', 'data', 'app.db')):
        db.create_all()
        print('Populating a default admin account...')
        user = User(firstname='Jane', lastname='Doe', username='jane', email='jane@labtycoon.com')
        user.password = '1234'
        for role in ['admin', 'approver', 'reporter']:
            r = UserRole(role_need=role)
            db.session.add(r)
            user.roles.append(r)
        db.session.add(user)

        print('Populating customers database...')
        for i in range(100):
            hn = fake.random_number(digits=10)
            gender = random.choice(['male', 'female'])
            firstname = fake.first_name()
            lastname = fake.last_name()
            address = fake.address()
            dob = fake.date_of_birth()
            db.session.add(Customer(
                hn=hn,
                gender=gender,
                firstname=firstname,
                lastname=lastname,
                address=address,
                dob=dob,
            ))
        db.session.commit()


sg.theme('SystemDefault')

layout = [
    [sg.Text('Lab Tycoon V.2022.1', font=('Arial', 34))],
    [sg.Text('A Demonstration Lab Information System for Education', font=('Arial', 20))],
    [sg.Text('By Faculty of Medial Technology, Mahidol University', font=('Arial', 16))],
    [sg.Text('โปรแกรมนี้พัฒนาสำหรับใช้ในการเรียนการสอนเท่านั้น '
             'ทางผู้พัฒนาไม่รับประกันความเสียหายที่อาจเกิดขึ้นหากนำไปใช้ในห้องปฏิบัติการจริง', font=('Arial', 14))],
    [sg.Button('Register', key='-REGISTER-'),
     sg.Button('Edit profile', key='-EDIT-PROFILE-', visible=True),
     sg.Button('Sign In', key='-SIGNIN-'),
     sg.Button('Sign Out', key='-SIGNOUT-', visible=False),
     sg.Button('Admin', key='-ADMIN-'),
     sg.Button('About', key='-ABOUT-'),
     sg.Button('SQL Tool', key='-sql-'),
     sg.Button('Analyze', key='-ANALYZE-'),
     sg.Button('Order List', key='-order-list-'),
     sg.Button('Logs', key='-LOGGING-'),
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
        access_token = create_signin_window()
        if access_token:
            window.find_element('-SIGNOUT-').update(visible=True)
            window.find_element('-EDIT-PROFILE-').update(visible=True)
            window.find_element('-SIGNIN-').update(visible=False)
    elif event == '-SIGNOUT-':
        if sg.popup_yes_no('You sure want to sign out?') == 'Yes':
            headers = {'Authorization': f'Bearer {access_token}'}
            resp = requests.delete('http://127.0.0.1:5000/auth/sign-out', headers=headers)
            if resp.status_code == 200:
                sg.popup_auto_close('You have logged out.')
                # TODO: store current_user in the database so that it is accessible outside Flask
                window.find_element('-SIGNOUT-').update(visible=False)
                window.find_element('-SIGNIN-').update(visible=True)
                window.find_element('-EDIT-PROFILE-').update(visible=True)
            else:
                sg.popup_error(resp.json().get('message'))
    elif event == '-EDIT-PROFILE-':
        if access_token:
            create_profile_window(access_token)
        else:
            sg.popup_error('Please sign in to access this section.', title='Access Denied')
    elif event == '-ADMIN-':
        if access_token:
            create_admin_window(access_token)
        else:
            sg.popup_error('Access denied. Please sign in as an admin.')
    elif event == '-ABOUT-':
        sg.popup_ok('This program is developed by Likit Preeyanon. '
                    'Please contact likit.pre@mahidol.edu for more information.', title='About')
    elif event == '-sql-':
        if not access_token:
            sg.popup_error('Please sign in to access this section.', title='Access Denied')
        else:
            create_sql_window()
    elif event == '-ANALYZE-':
        create_analysis_window(access_token)
    elif event == '-order-list-':
        if not access_token:
            sg.popup_error('Please sign in to access this section.', title='Access Denied')
        else:
            create_order_list_window(access_token)
    elif event == '-LOGGING-':
        if not access_token:
            sg.popup_error('Please sign in to access this section.', title='Access Denied')
        else:
            create_logging_window(access_token)

window.close()
