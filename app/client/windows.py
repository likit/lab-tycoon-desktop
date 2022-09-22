import logging
import os
from datetime import datetime

from sqlalchemy import create_engine

from server.extensions import db
from server.main import app
from sql_formatter.core import format_sql
import pandas as pd
from tabulate import tabulate

from http import HTTPStatus

import PySimpleGUI as sg
import requests

logger = logging.getLogger('client')


def format_datetime(isodatetime, datetime_format='%d/%m/%Y %H:%M:%S'):
    """A helper function that converts isodatetime to a datetime with a given format."""
    try:
        return datetime.fromisoformat(isodatetime).strftime(datetime_format)
    except:
        return isodatetime


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


def create_signin_window():
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
                logger.info('USER %s SIGNED IN' % values['username'])
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
            resp = requests.patch('http://127.0.0.1:5000/api/users', json=values, headers=headers)
            message = resp.json().get('message')
            if resp.status_code == 200:
                sg.popup_ok(f'{message}')
            else:
                sg.popup_error(f'{message}')
            break
    window.close()


def create_user_list_window(access_token):
    if not access_token:
        sg.popup_error('Please sign in to access this section.', title='Unauthorization Error')
        return
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get('http://127.0.0.1:5000/api/admin/users', headers=headers)
    data = []
    if resp.status_code != 200:
        sg.popup_error(resp.json().get('message'), title='System Error')
    else:
        for user in resp.json().get('data'):
            data.append([
                user['firstname'], user['lastname'], user['license_id'], user['username'], user['position'],
                user['roles'], user['active']
            ])
        layout = [
            [sg.Table(headings=['First', 'Last', 'License ID', 'Username', 'Position', 'Roles', 'Active'],
                      values=data, key='-TABLE-', enable_events=True)],
            [sg.Exit()]
        ]

        window = sg.Window('Users', layout=layout, resizable=True, modal=True, finalize=True)
        window['-TABLE-'].bind("<Double-Button-1>", " Double")

        while True:
            event, values = window.read()
            if event in ['Exit', sg.WIN_CLOSED]:
                break
            elif event == '-TABLE- Double' and values['-TABLE-']:
                username = data[values['-TABLE-'][0]][3]
                updated = create_admin_user_role_window(access_token, username)
                if updated:
                    data[values['-TABLE-'][0]][5] = ','.join([r for r, v in updated.items()
                                                              if v is True and r != '-ACTIVE-'])
                    data[values['-TABLE-'][0]][6] = updated['-ACTIVE-']
                window.find_element('-TABLE-').update(values=data)
                window.refresh()
        window.close()


TMLT_ACCESS_TOKEN_URL = 'https://tmlt.this.or.th/tmltapi/api/TmltToken/GetToken'
TMLT_SEARCH_URL = 'https://tmlt.this.or.th/tmltapi/10686/search'
HOSPITAL_CODE = '10686'


def create_tmlt_test_window(access_token):
    if not access_token:
        sg.popup_error('Please sign in to access this section.', title='Unauthorization Error')
        return
    resp = requests.post(TMLT_ACCESS_TOKEN_URL, json={'hospcode': HOSPITAL_CODE, 'provinceId': '12', 'amp': '01'})
    if resp.status_code == 200:
        tmlt_access_token = resp.json().get('token')
    else:
        sg.popup_error(resp.status_code)
        return

    layout = [
        [sg.Text('Search Term'), sg.InputText(key='search')],
        [sg.Button('Submit'), sg.CloseButton('Close')],
        [sg.Table(values=[],
                  headings=['TMLT Code', 'TMLT Name', 'Specimens', 'Method', 'Unit'],
                  key='-TABLE-', auto_size_columns=True, expand_x=True, expand_y=True, enable_events=True)]
    ]

    window = sg.Window('TMLT Test Search', layout=layout, modal=True, resizable=True, finalize=True)
    window['-TABLE-'].bind("<Double-Button-1>", " Double")

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
        elif event == '-TABLE- Double':
            try:
                row = values['-TABLE-'][0]
            except IndexError:
                pass
            else:
                create_tmlt_test_form_window(records[row], access_token)
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
    fullname = profile.get('firstname') + ' ' + profile.get('lastname')
    roles = profile.get('roles').split(',')
    layout = [
        [sg.Text('Username:'), sg.Text(username),
         sg.Text('Name:'), sg.Text(fullname)],
        [sg.Text('License ID:'), sg.Text(profile.get('license_id')),
         sg.Text('Position:'), sg.Text(profile.get('position'))],
        [sg.Checkbox('Active', default=profile.get('active'), key='-ACTIVE-', enable_events=True)],
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

    updated = None
    window = sg.Window('User Roles', layout, modal=True)
    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Update':
            error = False
            headers = {'Authorization': f'Bearer {access_token}'}
            if updated:
                resp = requests.patch(f'http://127.0.0.1:5000/api/admin/users/{username}/roles',
                                      headers=headers, json=updated)
                if resp.status_code != 201:
                    error = True

            resp = requests.patch(f'http://127.0.0.1:5000/api/users/{username}',
                                  headers=headers, json={'active': values['-ACTIVE-']})
            if resp.status_code != 200:
                error = True
            if error:
                sg.popup_error('Could not update the account.', 'Server Error')
            break
        else:
            updated = values
    window.close()
    return updated


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
        [sg.Button('Add', button_color=('white', 'green')), sg.CloseButton('Close')],
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
        [sg.Button('Add', button_color=('white', 'green')), sg.CloseButton('Close', size=(8, 1))]
    ]

    window = sg.Window('Test Form', layout=layout, modal=True)

    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Add':
            if access_token:
                headers = {'Authorization': f'Bearer {access_token}'}
                resp = requests.post(f'http://127.0.0.1:5000/api/admin/tests', headers=headers, json=values)
                if resp.status_code == HTTPStatus.CREATED:
                    logger.info(f'ADD NEW TEST: {values["code"]}')
                    sg.popup_ok(resp.json().get('message'))
                    break
                else:
                    sg.popup_error(resp.json().get('message'))
            else:
                sg.popup_error('Access denied')
    window.close()


def load_all_tests(access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(f'http://127.0.0.1:5000/api/admin/tests', headers=headers)
    tests = []
    if resp.status_code == HTTPStatus.OK:
        for t in resp.json().get('data'):
            tests.append([
                t['code'],
                t['label'],
                t['desc'],
                t['tmlt_code'],
                t['tmlt_name'],
                t['specimens'],
                t['method'],
                t['unit'],
                t['price'],
                t['active'],
            ])
    return tests, resp.status_code


def create_test_list_window(access_token):
    tests, status_code = load_all_tests(access_token)
    if not tests or status_code != HTTPStatus.OK:
        sg.popup_error(status_code)
    layout = [
        [sg.Table(values=tests,
                  headings=['Code', 'Label', 'Description',
                            'TMLT Code', 'TMLT Name', 'Specimens',
                            'Method', 'Unit', 'Price', 'Active'],
                  key='-TABLE-', expand_x=True, expand_y=True, enable_events=True)],
        [sg.Button('Add', button_color=('white', 'green')), sg.CloseButton('Close')],
    ]

    window = sg.Window('All Tests', layout=layout, modal=True, resizable=True)

    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Add':
            if access_token:
                create_tmlt_test_window(access_token)
                tests, status_code = load_all_tests(access_token)
                if not tests or status_code != HTTPStatus.OK:
                    sg.popup_error(status_code)
                else:
                    window.find_element('-TABLE-').update(values=tests)
                    window.refresh()
            else:
                sg.popup_error('Access denied')
    window.close()


def show_save_query_dialog():
    layout = [
        [sg.Input(key='-filepath-'), sg.FileSaveAs('Browse', file_types=(('Excel', 'xlsx'),))],
        [sg.Ok()]
    ]
    dialog = sg.Window('Save As', layout, modal=True)
    filepath = ''
    while True:
        event, values = dialog.read()
        if event in (sg.WIN_CLOSED,):
            break
        elif event == 'Ok':
            filepath = values['-filepath-']
            break

    dialog.close()
    return filepath


with app.app_context():
    engine = create_engine(db.engine.url)


def create_sql_window():
    layout = [
        [sg.Text('Results'), ],
        [sg.Multiline(key='-table-', size=(80, 10), reroute_stdout=True, disabled=True,
                      font='Courier 13', horizontal_scroll=True, expand_x=True, expand_y=True)],
        [sg.Button('Save Data', key='-save-data-')],
        [sg.Text('SQL Query')],
        [sg.Multiline(key='-query-', size=(80, 10), expand_y=True, expand_x=True,
                      focus=True, font='Courier 13 bold', text_color='blue')],
        [sg.Button('Run'), sg.Button('Format'), sg.Button('Save Query', key='-save-query-'),
         sg.Button('Clear'), sg.Button('Exit')],
        [sg.Text('Console')],
        [sg.Multiline(key='-console-', size=(80, 5), font='Courier 13 bold', expand_x=True, expand_y=True)]
    ]

    window = sg.Window('SQL Tools', layout=layout, modal=True, resizable=True)

    df = pd.DataFrame()

    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED):
            break
        elif event == 'Format':
            window['-query-'].update(format_sql(values['-query-']))
        elif event == 'Clear':
            window['-query-'].update('')
        elif event == '-save-query-':
            filepath = show_save_query_dialog()
            if filepath:
                if not filepath.endswith('sql'):
                    filepath += '.sql'
                    fo = open(filepath, 'w')
                    fo.write(values['-query-'])
                    fo.close()
                    sg.PopupQuickMessage('The query has been saved.')
            else:
                sg.PopupQuickMessage('No file has been chosen.')

        elif event == '-save-data-':
            if not df.empty:
                filepath = show_save_query_dialog()
                if filepath:
                    if not filepath.endswith('xlsx'):
                        filepath += '.xlsx'
                    df.to_excel(f'{filepath}', index=False)
                    sg.PopupQuickMessage('Data have been saved.')
            else:
                sg.PopupQuickMessage('The result data is empty.')

        elif event == 'Run':
            try:
                df = pd.read_sql_query(values['-query-'], con=engine)
            except Exception as e:
                window['-console-'].update(str(e), text_color_for_value='red')
                df = pd.DataFrame()
            else:
                window['-console-'].update(f'Total records = {len(df)}', text_color_for_value='green')
                window['-table-'].update('')
                print(tabulate(df, headers='keys', tablefmt='psql'))

    window.close()


def create_analysis_window(access_token):
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(f'http://127.0.0.1:5000/api/order-items', headers=headers, params={'unfinished': 'true'})
    if resp.status_code == 200:
        items = []
        data = resp.json().get('data')
        for item in data:
            items.append([
                item['id'],
                item['code'],
                item['label'],
                item['tmlt_name'],
                format_datetime(item['received_at']),
                item['hn'],
                item['patient']
            ])
    layout = [
        [sg.Table(headings=['ID', 'Code', 'Label', 'TMLT Name', 'Received At', 'HN', 'Patient'],
                  values=items, key='-TABLE-', enable_events=True)],
        [sg.Button('Run', button_color=('white', 'green')), sg.CloseButton('Close'), sg.Help()],
    ]

    window = sg.Window('Analysis', layout=layout, modal=True, resizable=True)

    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED):
            break
        elif event == 'Run':
            headers = {'Authorization': f'Bearer {access_token}'}
            resp = requests.get(f'http://127.0.0.1:5000/api/analyses', headers=headers)
            sg.popup_ok(resp.json().get('message'))
        elif event == 'Help':
            sg.popup_ok('The list shows all test that waiting to be analyzed.'
                        ' If you click run, all tests will be sent to virtual analyzers.')
    window.close()


def create_order_list_window(access_token):
    headers = {'Authorization': f'Bearer {access_token}'}

    def load_orders():
        resp = requests.get(f'http://127.0.0.1:5000/api/orders', headers=headers)
        data = []
        if resp.status_code == 200:
            for order in resp.json().get('data'):
                data.append([
                    order['id'],
                    order['hn'],
                    format_datetime(order['order_datetime']),
                    format_datetime(order['received_datetime']),
                    format_datetime(order['rejected_datetime']),
                    order['rejected_by'],
                    format_datetime(order['cancelled_datetime']),
                    order['cancelled_by'],
                    order['firstname'],
                    order['lastname'],
                    order['items'],
                ])
        return data

    data = load_orders()

    layout = [
        [sg.Table(values=data, headings=['ID', 'HN', 'Order At', 'Received At',
                                         'Rejected At', 'Rejected By', 'Cancelled At', 'Cancelled By',
                                         'Firstname', 'Lastname', 'Items'],
                  key="-ORDER-TABLE-", auto_size_columns=True,
                  expand_x=True, expand_y=True,
                  enable_events=True,
                  num_rows=20,
                  )],
        [sg.Button('Get Order', key='-GET-ORDER-'), sg.CloseButton('Close')]
    ]

    window = sg.Window('Order List', layout=layout, modal=True, resizable=True, finalize=True)
    window['-ORDER-TABLE-'].bind("<Double-Button-1>", " Double")
    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED):
            break
        elif event == '-ORDER-TABLE- Double' and values['-ORDER-TABLE-']:
            create_order_item_list_window(access_token, data[values['-ORDER-TABLE-'][0]][0])
            data = load_orders()
            window.find_element('-ORDER-TABLE-').update(values=data)
        elif event == '-GET-ORDER-':
            headers = {'Authorization': f'Bearer {access_token}'}
            # TODO: add code to check if the simulations run successfully
            resp = requests.get(f'http://127.0.0.1:5000/api/simulations', headers=headers)
            resp = requests.get(f'http://127.0.0.1:5000/api/orders', headers=headers)
            data = load_orders()
            window.find_element('-ORDER-TABLE-').update(values=data)
    window.close()


def create_reject_reason_window():
    layout = [
        [sg.Text('Please select the reason:')],
        [sg.Combo(['Improper specimens collection', 'Not enough specimens', 'Tests not available'], key='-REASON-')],
        [sg.Multiline(size=(40, 10), key='-COMMENT-')],
        [sg.Ok('Submit'), sg.Cancel('Cancel')]
    ]
    window = sg.Window('Ordered Item List', layout=layout, modal=True, finalize=True)
    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED, 'Cancel', 'Submit'):
            break
    window.close()
    return values.get('-REASON-'), values.get('-COMMENT-')


def create_order_item_list_window(access_token, lab_order_id):
    headers = {'Authorization': f'Bearer {access_token}'}

    def load_item_list():
        resp = requests.get(f'http://127.0.0.1:5000/api/orders/{lab_order_id}', headers=headers)
        if resp.status_code == 200:
            items = []
            data = resp.json().get('data')
            for item in data['items']:
                items.append([
                    item['id'],
                    item['code'],
                    item['tmlt_name'],
                    item['value_string'],
                    format_datetime(item['reported_at']),
                    item['reporter_name'],
                    format_datetime(item['approved_at']),
                    item['approver_name'],
                    format_datetime(item['finished_at']),
                    format_datetime(item['cancelled_at']),
                ])
            return data, items
        else:
            sg.popup_error(f"{resp.json().get('message')}")
            return None, None

    data, items = load_item_list()

    is_order_rejected = data['rejected_at'] is not None
    is_order_cancelled = data['cancelled_at'] is not None

    if data:
        desc = """
        The table displays a list of items in the order. Click the Reject button to reject the order or click the Cancel button to cancel the order.
        """
        layout = [
            [sg.Text('HN:'), sg.Text(data['hn']),
             sg.Text('Name:'), sg.Text(f"{data['firstname']} {data['lastname']}"),
             sg.Text('Ordered At:'), sg.Text(f"{format_datetime(data['order_datetime'])}"),
             ],
            [sg.Table(values=items, headings=['Item ID', 'Code', 'Name', 'Result', 'Reported At',
                                              'Reporter', 'Approved At', 'Approver', 'Finished At', 'Cancelled At'],
                      key="-ORDER-ITEM-TABLE-",
                      auto_size_columns=True,
                      expand_y=True,
                      expand_x=True,
                      enable_events=True,
                      )],
            [sg.Button('Reject', button_color=('white', 'red'),
                       disabled_button_color=('white', 'lightgrey'),
                       disabled=is_order_rejected or is_order_cancelled),
             sg.Button('Cancel', button_color=('white', 'red'),
                       disabled_button_color=('white', 'lightgrey'),
                       disabled=is_order_cancelled or is_order_rejected),
             sg.CloseButton('Close')]
        ]
        window = sg.Window('Ordered Item List', layout=layout, modal=True, finalize=True, resizable=True)
        window['-ORDER-ITEM-TABLE-'].bind("<Double-Button-1>", " Double")
        while True:
            event, values = window.read()
            if event in ('Exit', sg.WIN_CLOSED):
                break
            elif event == '-ORDER-ITEM-TABLE- Double' and values['-ORDER-ITEM-TABLE-']:
                create_item_detail_window(access_token, items[values['-ORDER-ITEM-TABLE-'][0]][0])
                data, items = load_item_list()
                window.find_element('-ORDER-ITEM-TABLE-').update(values=items)
            elif event == 'Reject':
                resp = sg.popup_ok_cancel('Are you sure want to reject this order?', title='Order Rejection')
                if resp == 'OK':
                    update_data = {'rejected_at': datetime.now().isoformat()}
                    reason, comment = create_reject_reason_window()
                    if reason:
                        update_data['reason'] = reason
                    if comment:
                        update_data['comment'] = comment
                    resp = requests.patch(f'http://127.0.0.1:5000/api/orders/{lab_order_id}',
                                          headers=headers, json=update_data)
                    if resp.status_code == HTTPStatus.OK:
                        sg.popup_ok('The order has been rejected.')
                        break
                    else:
                        sg.popup_error('Failed to reject the order.', title='System Error')
            elif event == 'Cancel':
                resp = sg.popup_ok_cancel('Are you sure want to cancel this order?', title='Order Cancellation')
                if resp == 'OK':
                    update_data = {'cancelled_at': datetime.now().isoformat()}
                    resp = requests.patch(f'http://127.0.0.1:5000/api/orders/{lab_order_id}',
                                          headers=headers, json=update_data)
                    if resp.status_code == HTTPStatus.OK:
                        sg.popup_ok('The order has been cancelled.')
                        break
                    else:
                        sg.popup_error('Failed to cancel the order.', title='System Error')
        window.close()
    else:
        return


def create_logging_window(access_token):
    logs = open('run_log.txt').readlines()
    layout = [
        [sg.Multiline('\n'.join(logs), size=(80, 10), disabled=True,
                      font='Courier 13', horizontal_scroll=True, expand_x=True, expand_y=True)],
        [sg.CloseButton('Close')],
    ]
    window = sg.Window('Program Logs', layout=layout, modal=True, resizable=True)
    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED):
            break
    window.close()


def create_lab_order_item_version_list_window(access_token, item):
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(f'http://127.0.0.1:5000/api/order-items/{item["id"]}/versions',
                        headers=headers)
    versions = []
    if resp.status_code == 200:
        for n, ver in enumerate(resp.json().get('data'), start=1):
            versions.append([
                n,
                ver['value'],
                format_datetime(ver['reported_at']),
                ver['reporter_name'],
                format_datetime(ver['approved_at']),
                ver['approver_name'],
                ver['comment'],
                format_datetime(ver['updated_at']),
                ver['updater_name'],
            ])
    else:
        sg.popup_error(f'{resp.json().get("message")}', title='Server Error')
        return

    layout = [
        [sg.Text('ID: '), sg.Text(item['id']),
         sg.Text('Label: '), sg.Text(item['label']),
         sg.Text('HN: '), sg.Text(item['hn']),
         sg.Text('Patient: '), sg.Text(item['patient']),
         ],
        [sg.Table(values=versions, headings=['Version', 'Value', 'Reported At',
                                             'Reporter', 'Approved At', 'Approver', 'Comment',
                                             'Updated At', 'Updater'],
                  key="-VERSION-TABLE-", enable_events=True)
         ],
        [sg.CloseButton('Close')]
    ]
    window = sg.Window('Lab Order Item Detail', layout=layout, modal=True, resizable=True)
    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED):
            break
    window.close()


def create_item_detail_window(access_token, item_id):
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(f'http://127.0.0.1:5000/api/order-items/{item_id}', headers=headers)
    if resp.status_code != 200:
        sg.popup_error('Cannot load item detail.')
        return

    item = resp.json().get('data')

    actions = [sg.Button('Update', button_color=('white', 'green')), sg.Cancel(button_color=('white', 'red')),
               sg.Help()]

    if item['approved_at']:
        pass
    elif item['reported_at']:
        actions.insert(0, sg.Button('Approve', button_color=('white', 'green')))
    elif item['finished_at']:
        actions.insert(0, sg.Button('Report', button_color=('white', 'green')))

    if item['cancelled_at']:
        is_item_cancelled = True
        actions = []
    else:
        is_item_cancelled = False

    layout = [
        [sg.Text('ID', size=(8, 1)), sg.Text(item_id),
         sg.Text('Code', size=(8, 1)), sg.Text(item['code']),
         sg.Text('HN: '), sg.Text(item['hn']),
         sg.Text('Patient: '), sg.Text(item['patient']),
         ],
        [sg.Text('Value'), sg.Input(item['value'], key='-ITEM-VALUE-', disabled=is_item_cancelled)],
        [sg.Text('Comment')],
        [sg.Multiline(item['comment'], key='-UPDATE-COMMENT-', size=(45, 10), disabled=is_item_cancelled)],
        actions,
        [sg.Button('History'), sg.CloseButton('Close', button_color=('white', 'red'))],
    ]
    window = sg.Window('Lab Order Item Detail', layout=layout, modal=True, resizable=True)
    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED):
            break
        elif event == 'Cancel':
            response = sg.popup_ok_cancel('Are you sure want to cancel this item?')
            if response == 'OK':
                resp = requests.patch(f'http://127.0.0.1:5000/api/order-items/{item_id}',
                                      headers=headers,
                                      json={'cancelled_at': datetime.now().isoformat()})
                if resp.status_code == 200:
                    sg.popup_ok(f'{resp.json().get("message")}')
                    break
                else:
                    sg.popup_error(f'{resp.json().get("message")}', title='System Error')
        elif event == 'Report':
            response = sg.popup_ok_cancel('Are you sure want to report this item?')
            if response == 'OK':
                resp = requests.patch(f'http://127.0.0.1:5000/api/order-items/{item_id}',
                                      headers=headers,
                                      json={'reported_at': datetime.now().isoformat()})
                if resp.status_code == 200:
                    sg.popup_ok(f'{resp.json().get("message")}')
                    break
                else:
                    sg.popup_error(f'{resp.json().get("message")}', title='Unauthorized')
        elif event == 'Approve':
            response = sg.popup_ok_cancel('Are you sure want to approve this item?')
            if response == 'OK':
                resp = requests.patch(f'http://127.0.0.1:5000/api/order-items/{item_id}',
                                      headers=headers,
                                      json={'approved_at': datetime.now().isoformat()})
                if resp.status_code == 200:
                    sg.popup_ok(f'{resp.json().get("message")}')
                    break
                else:
                    sg.popup_error(f'{resp.json().get("message")}', title='Unauthorized')
        elif event == 'Update':
            response = sg.popup_ok_cancel('Are you sure want to update this item?')
            if response == 'OK':
                resp = requests.patch(f'http://127.0.0.1:5000/api/order-items/{item_id}',
                                      headers=headers,
                                      json={'_value': values['-ITEM-VALUE-'],
                                            'comment': values['-UPDATE-COMMENT-'],
                                            'finished_at': datetime.now().isoformat(),
                                            'reported_at': datetime.now().isoformat()})
                if resp.status_code == 200:
                    sg.popup_ok(f'{resp.json().get("message")}')
                    break
                else:
                    sg.popup_error(f'{resp.json().get("message")}', title='Unauthorized')

        elif event == 'History':
            create_lab_order_item_version_list_window(access_token, item)
    window.close()


def create_customer_list_window(access_token):
    if not access_token:
        sg.popup_error('Please sign in to access this section.', title='Unauthorization Error')
        return
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get('http://127.0.0.1:5000/api/customers', headers=headers)
    data = []
    if resp.status_code != 200:
        sg.popup_error(resp.json().get('message'), title='System Error')
    else:
        for customer in resp.json().get('data'):
            data.append([
                customer['id'], customer['hn'], customer['fullname'], customer['dob']
            ])
        layout = [
            [sg.Text('Search'), sg.InputText(key='-QUERY-'), sg.Button('Go'),
             sg.Button('Clear', button_color=('white', 'red'))],
            [sg.Table(headings=['ID', 'HN', 'Name', 'DOB'],
                      values=data, key='-CUSTOMER-TABLE-', enable_events=True, expand_x=True, expand_y=True)],
            [sg.Exit()]
        ]

        flt_data = data
        window = sg.Window('Users', layout=layout, resizable=True, modal=True, finalize=True)
        window['-CUSTOMER-TABLE-'].bind("<Double-Button-1>", " Double")

        while True:
            event, values = window.read()
            if event in ['Exit', sg.WIN_CLOSED]:
                break
            elif event == '-CUSTOMER-TABLE- Double' and values['-CUSTOMER-TABLE-']:
                customer_id = flt_data[values['-CUSTOMER-TABLE-'][0]][0]
                create_customer_order_list_window(access_token, customer_id)
            elif event == 'Go':
                flt_data = []
                for cust in data:
                    if values['-QUERY-'] in cust[1] or values['-QUERY-'] in cust[2]:
                        flt_data.append(cust)
                if not flt_data:
                    flt_data = data
                window.find_element('-CUSTOMER-TABLE-').update(values=flt_data)
            elif event == 'Clear':
                window.find_element('-QUERY-').update('')
                window.find_element('-CUSTOMER-TABLE-').update(values=data)
        window.close()


def create_customer_order_list_window(access_token, customer_id):
    if not access_token:
        sg.popup_error('Please sign in to access this section.', title='Unauthorization Error')
        return
    headers = {'Authorization': f'Bearer {access_token}'}
    resp = requests.get(f'http://127.0.0.1:5000/api/customers/{customer_id}/orders', headers=headers)
    if resp.status_code != 200:
        sg.popup_error(resp.json().get('message'), title='System Error')
        return
    else:
        treedata = sg.TreeData()
        data = resp.json().get('data')
        for order in data['orders']:
            treedata.insert('', order['id'], order['id'], [format_datetime(order['received_at'])])
            for item in order['items']:
                treedata.insert(order['id'], item['id'], item['id'],
                                ['', item['code'], item['label'], item['value'],
                                 format_datetime(item['finished_at']),
                                 format_datetime(item['reported_at']), item['reporter_name'],
                                 item['approved_at'], item['approver_name'],
                                 ])
        layout = [
            [sg.Tree(data=treedata, headings=['Received At', 'Code', 'Label', 'Value',
                                              'Finished At', 'Reported At', 'Reporter',
                                              'Approved At', 'Approver'],
                     auto_size_columns=True, show_expanded=False, expand_y=True, expand_x=True)],
            [sg.CloseButton('Close')]
        ]

        window = sg.Window('Customer Orders', layout=layout, resizable=True, modal=True, finalize=True)
        while True:
            event, values = window.read()
            if event in ['Exit', sg.WIN_CLOSED]:
                break
        window.close()
