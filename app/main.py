import PySimpleGUI as sg
import threading
import requests
from client.windows import create_register_window
from server.main import app

from server.models import *

with app.app_context():
    db.create_all()

sg.theme('DarkAmber')

layout = [
    [sg.Text('Welcome to Lab Tycoon!', font=('Arial', 34))],
    [sg.Text('A Demonstration Lab Information System for Education', font=('Arial', 20))],
    [sg.Text('Version 2022.1', font=('Arial', 20))],
    [sg.Text('By Faculty of Medial Technology, Mahidol University', font=('Arial', 16))],
    [sg.Text('โปรแกรมนี้พัฒนาสำหรับใช้ในการเรียนการสอนเท่านั้น ทางผู้พัฒนาไม่รับประกันความเสียหายที่อาจเกิดขึ้นหากนำไปใช้ในห้องปฏิบัติการจริง', font=('Arial', 14))],
    [sg.Button('Register', key='-REGISTER-'), sg.Exit(button_color='white on red')]
]

window = sg.Window('Lab Tycoon Desktop!', layout=layout, element_justification='center').finalize()
window.maximize()
flask_job = threading.Thread(target=lambda: app.run(use_reloader=False, debug=True))
flask_job.start()

while True:
    event, values = window.read()
    if event == sg.WINDOW_CLOSED or event == 'Exit':
        print('Trying to terminate Flask app')
        requests.get('http://127.0.0.1:5000/kill')
        flask_job.join()
        break
    elif event == '-REGISTER-':
        create_register_window()

window.close()
