import PySimpleGUI as sg
import requests


def create_register_window():
    layout = [
        [sg.Text('First Name', size=(8, 1)), sg.InputText(key='firstname')],
        [sg.Text('Last Name', size=(8, 1)), sg.InputText(key='lastname')],
        [sg.Text('Email', size=(8, 1)), sg.InputText(key='email')],
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
            resp = requests.post('http://127.0.0.1:5000/api/users', json=values)
            message = resp.json().get('message')
            if resp.status_code == 201:
                sg.popup_ok(f'{message}')
                break
            else:
                sg.popup_error(f'{message}')
    window.close()


def create_singin_window():
    layout = [
        [sg.Text('Username', size=(8, 1)), sg.InputText(focus=True, key='username')],
        [sg.Text('Password', size=(8, 1)), sg.Input(password_char='*', key='password')],
        [sg.Button('Submit'), sg.Exit()],
    ]

    window = sg.Window('Sign In', layout=layout, modal=True)
    access_token = ''

    while True:
        event, values = window.read()
        if event in ['Exit', sg.WIN_CLOSED]:
            break
        else:
            resp = requests.post('http://127.0.0.1:5000/auth/sign-in', json=values)
            access_token = resp.json().get('access_token')
            message = resp.json().get('message')
            if resp.status_code == 200:
                sg.popup_ok(f'{message}')
                break
            else:
                sg.popup_error(f'{message}')
    window.close()
    return access_token


def create_profile_window(access_token):
    if not access_token:
        return
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get('http://127.0.0.1:5000/api/users', headers=headers)
    if resp.status_code != 200:
        if resp.status_code == 401:
            sg.popup_error('Please sign in to access this part.')
        else:
            sg.popup_error(f'Error occurred: {resp.status_code}')
        return
    profile = resp.json()

    layout = [
        [sg.Text('Username', size=(8, 1)), sg.InputText(profile['username'], disabled=True, key='username')],
        [sg.Text('First Name', size=(8, 1)), sg.InputText(profile['firstname'], key='firstname')],
        [sg.Text('Last Name', size=(8, 1)), sg.InputText(profile['lastname'], key='lastname')],
        [sg.Text('Email', size=(8, 1)), sg.InputText(profile['email'], key='email')],
        [sg.Text('License ID', size=(8, 1)), sg.InputText(profile['license_id'], key='license_id')],
        [sg.Button('Submit'), sg.Exit()],
    ]

    window = sg.Window('User Profile', layout=layout, modal=True)

    while True:
        event, values = window.read()
        if event in ['Exit', sg.WIN_CLOSED]:
            break
        else:
            resp = requests.put('http://127.0.0.1:5000/api/users', json=values, headers=headers)
            message = resp.json().get('message')
            if resp.status_code == 201:
                sg.popup_ok(f'{message}')
            else:
                sg.popup_error(f'{message}')
            break
    window.close()


def create_user_list_window(access_token):
    if not access_token:
        return
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get('http://127.0.0.1:5000/api/admin/users', headers=headers)
    data = []
    for user in resp.json().get('data'):
        data.append([
            user['firstname'], user['lastname'], user['license_id'], user['username'], user['roles']
        ])
    layout = [
        [sg.Table(headings=['First', 'Last', 'License ID', 'Username', 'Roles'],
                  values=data, key='-TABLE-', enable_events=True)],
        [sg.Exit()]
    ]

    window = sg.Window('Users', layout=layout, resizable=True, modal=True)

    while True:
        event, values = window.read()
        if event in ['Exit', sg.WIN_CLOSED]:
            break
        elif event == '-TABLE-':
            print(event, values)
            if values['-TABLE-']:
                username = data[values['-TABLE-'][0]][3]
                updated_roles = create_admin_user_role_window(access_token, username)
                if updated_roles:
                    data[values['-TABLE-'][0]][4] = ','.join([r for r,v in updated_roles.items() if v is True])
                window.find_element('-TABLE-').update(values=data)
                window.refresh()
    window.close()


def create_admin_user_role_window(access_token, username):
    if not access_token:
        return
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(f'http://127.0.0.1:5000/api/users/{username}', headers=headers)
    if resp.status_code != 200:
        sg.popup_error(f'Error occurred: {resp.status_code}')
        return
    profile = resp.json()
    roles = profile.get('roles').split(',')
    layout = [
        [sg.Text('Username: '), sg.Text(username)],
        [sg.Text('Role:')],
    ]

    for role in ['admin', 'approver', 'reporter']:
        if role in roles:
            layout.append([
                sg.Checkbox(role.title(), key=role, default=True, enable_events=True)
            ])
        else:
            layout.append([
                sg.Checkbox(role.title(), key=role, default=False, enable_events=True)
            ])

    layout.append(
        [sg.Button('Update'), sg.CloseButton('Close')]
    )

    updated_roles = None
    window = sg.Window('User Roles', layout, modal=True)
    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Update':
            if updated_roles is not None:
                headers = {'Authorization': f'Bearer {access_token}'}
                resp = requests.put(f'http://127.0.0.1:5000/api/admin/users/{username}/roles',
                                    headers=headers, json=updated_roles)
                if resp.status_code == 201:
                    sg.popup_ok(resp.json().get('message'))
                    break
                else:
                    sg.popup_error(resp.status_code)
            else:
                sg.popup_ok('No change detected.')
        else:
            updated_roles = values
    window.close()
    return updated_roles
