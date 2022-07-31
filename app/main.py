import PySimpleGUI as sg
import threading
import requests
from server.main import app


sg.theme('DarkAmber')

layout = [
    [sg.Text('Loading Flask application')],
    [sg.Exit()]
]

window = sg.Window('Lab Tycoon Desktop!', layout=layout)
flask_job = threading.Thread(target=lambda: app.run(use_reloader=False, debug=True))
flask_job.start()

while True:
    event, values = window.read()
    if event == sg.WINDOW_CLOSED or event == 'Exit':
        print('Trying to terminate Flask app')
        requests.get('http://127.0.0.1:5000/kill')
        flask_job.join()
        break
