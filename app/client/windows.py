from http import HTTPStatus

import PySimpleGUI as sg
import requests


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
        [sg.Text('Position', size=(8, 1)), sg.InputText(profile['position'], key='position')],
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
            user['firstname'], user['lastname'], user['license_id'], user['username'], user['position'], user['roles']
        ])
    layout = [
        [sg.Table(headings=['First', 'Last', 'License ID', 'Username', 'Position', 'Roles'],
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
                    data[values['-TABLE-'][0]][5] = ','.join([r for r,v in updated_roles.items() if v is True])
                window.find_element('-TABLE-').update(values=data)
                window.refresh()
    window.close()


TMLT_ACCESS_TOKEN_URL = 'https://tmlt.this.or.th/tmltapi/api/TmltToken/GetToken'
TMLT_SEARCH_URL = 'https://tmlt.this.or.th/tmltapi/10686/search'
HOSPITAL_CODE = '10686'


def create_tmlt_test_window(access_token):
    resp = requests.post(TMLT_ACCESS_TOKEN_URL, json={'hospcode': HOSPITAL_CODE, 'provinceId': '12', 'amp': '01'})
    if resp.status_code == 200:
        tmlt_access_token = resp.json().get('token')
    else:
        sg.popup_error(resp.status_code)
        return

    layout = [
        [sg.Text('Search Term'), sg.InputText(key='search')],
        [sg.Button('Submit'), sg.CloseButton('Close')],
        [sg.Table(values=[], headings=['tmltCode', 'tmltName', 'specimens', 'method', 'unit'], key='-TABLE-',
                  expand_x=True, expand_y=True, enable_events=True)]
    ]

    window = sg.Window('TMLT Test Search', layout=layout, modal=True, resizable=True)

    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Submit':
            search = values.get('search')
            resp = requests.get(TMLT_SEARCH_URL,
                                headers={'Authorization': f'Bearer {tmlt_access_token}', 'Accept': 'application/json'},
                                params={'search': search}
                                )
            if resp.status_code == 200:
                records = []
                for rec in resp.json().get('tmltData'):
                    records.append([rec['tmltCode'],
                                    rec['tmltName'],
                                    rec['specimen'],
                                    rec['method'],
                                    rec['unit'],
                                    rec['cgdCode'],
                                    rec['cgdName'],
                                    rec['cgdPrice'],
                                    rec['orderType'],
                                    rec['scale'],
                                    rec['loincNum'],
                                    rec['panel'],
                                    rec['component'],
                                    ])
                window.find_element('-TABLE-').update(values=records)
                window.refresh()
            else:
                sg.popup_error('Error occurred. Could not fetch data from TMLT server.')
        elif event == '-TABLE-':
            try:
                row = values['-TABLE-'][0]
            except IndexError:
                pass
            else:
                create_tmlt_test_form_window(records[row], access_token)
    window.close()

    '''
    if not access_token:
        return
    
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(f'http://127.0.0.1:5000/api/users/{username}', headers=headers)
    if resp.status_code != 200:
        sg.popup_error(f'Error occurred: {resp.status_code}')
        return
    '''


def create_admin_window(access_token):
    menu_def = [
        ['&Samples', ['&Tests', '&BioSource', 'S&pecimens']],
        ['&Tests', ['&Add TMLT test']],
    ]
    layout = [
        [sg.Menu(menu_def)],
        [sg.Button('User Management', key='-USER-')],
        [sg.Button('Specimens', key='-SPECIMENS-')],
        [sg.CloseButton('Close')]
    ]

    window = sg.Window('Administration', layout=layout, modal=True)
    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == '-USER-':
            create_user_list_window(access_token)
        elif event == 'BioSource':
            create_biosource_window(access_token)
        elif event == 'Add TMLT test':
            create_tmlt_test_window(access_token)
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


def create_new_biosource_window():
    layout = [
        [sg.Text('Source'), sg.InputText(key='source')],
        [sg.CloseButton('Cancel'), sg.Ok()]
    ]

    window = sg.Window('New Biological Source', modal=True, layout=layout)
    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break


def create_biosource_window(access_token):
    if not access_token:
        return
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(f'http://127.0.0.1:5000/api/admin/biosources', headers=headers)
    if resp.status_code != 200:
        sg.popup_error(f'Error occurred: {resp.status_code}')
        return
    biosources = resp.json().get('data')

    data = []
    for src in biosources:
        data.append(src.get('source'))
    layout = [
        [sg.Table(data, headings=['source'], enable_events=True, key='-TABLE-')],
        [sg.Button('Add'), sg.CloseButton('Close')],
    ]

    window = sg.Window('Biological Sources', modal=True, layout=layout)

    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Add':
            pass
    window.close()


def create_tmlt_test_form_window(data, access_token):
    layout = [
        [sg.Text('Code', size=(8, 1)), sg.InputText(key='code')],
        [sg.Text('Label', size=(8, 1)), sg.InputText(key='label')],
        [sg.Text('Description', size=(8, 1)), sg.Multiline(data[1], size=(45, 10), key='desc')],
        [sg.Text('Price', size=(8, 1)), sg.InputText(key='price')],
        [sg.Text('TMLT Code', size=(8, 1)), sg.InputText(data[0], disabled=True, key='tmlt_code')],
        [sg.Text('TMLT Name', size=(8, 1)), sg.InputText(data[1], disabled=True, key='tmlt_name')],
        [sg.Text('Specimens', size=(8, 1)), sg.InputText(data[2], key='specimens')],
        [sg.Text('Component', size=(8, 1)), sg.InputText(data[12], key='component')],
        [sg.Text('Method', size=(8, 1)), sg.InputText(data[3], key='method')],
        [sg.Text('Unit', size=(8, 1)), sg.InputText(data[4], key='unit')],
        [sg.Text('CGD Code', size=(8, 1)), sg.InputText(data[5], disabled=True, key='cgd_code')],
        [sg.Text('CGD Name', size=(8, 1)), sg.InputText(data[6], key='cgd_name')],
        [sg.Text('CGD Price', size=(8, 1)), sg.InputText(data[7], key='cgd_price')],
        [sg.Text('Order Type', size=(8, 1)), sg.InputText(data[8], key='order_type')],
        [sg.Text('Scale', size=(8, 1)), sg.InputText(data[9], key='scale')],
        [sg.Text('LOINC Code', size=(8, 1)), sg.InputText(data[10], disabled=True, key='loinc_no')],
        [sg.Text('Panel', size=(8, 1)), sg.InputText(data[11], key='panel')],
        [sg.Text('Ref. Min', size=(8, 1)), sg.InputText(key='ref_min')],
        [sg.Text('Ref. Max', size=(8, 1)), sg.InputText(key='ref_max')],
        [sg.Text('Valuce Choices', size=(8, 1)), sg.Multiline(size=(45, 10), key='value_choices')],
        [sg.CloseButton('Close', size=(8, 1)), sg.Button('Add')]
    ]

    window = sg.Window('Test Form', layout=layout, modal=True)

    while True:
        event, values = window.read()
        print(event)
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Add':
            if access_token:
                headers = {'Authorization': f'Bearer {access_token}'}
                print(values)
                resp = requests.post(f'http://127.0.0.1:5000/api/admin/tests', headers=headers, json=values)
                if resp.status_code == HTTPStatus.CREATED:
                    sg.popup_ok(resp.json().get('message'))
                    break
                else:
                    sg.popup_error(resp.json().get('message'))
            else:
                sg.popup_error('Access denied')
    window.close()
