import PySimpleGUI as sg
import requests


def create_register_window():
    layout = [
        [sg.Text('First Name'), sg.InputText()],
        [sg.Text('Last Name'), sg.InputText()],
        [sg.Text('Email'), sg.InputText()],
        [sg.Text('Username'), sg.InputText()],
        [sg.Text('Password'), sg.Input(password_char='*')],
        [sg.Button('Submit'), sg.Exit()],
    ]

    window = sg.Window('Register', layout=layout)

    while True:
        event, values = window.read()
        if event in ['Exit', sg.WIN_CLOSED]:
            break
        else:
            print(values)
            resp = requests.post('http://127.0.0.1:5000/api/register', json=values)
            if resp.status_code == 200:
                window.close()
                sg.PopupOK('Registered successfully')
    window.close()
