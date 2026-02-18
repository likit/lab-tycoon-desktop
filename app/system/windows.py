from datetime import datetime
from http import HTTPStatus

import FreeSimpleGUI as sg
import pandas as pd
import requests
from sql_formatter.core import format_sql
from sqlalchemy import select
from sqlalchemy.orm import Session
from tabulate import tabulate

from app.auth.windows import login_required, SessionManager
from app.system.models import engine, Test, TestMethod, LabOrder
from app.config import logger


@login_required
def create_logging_window():
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


@login_required
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


def load_all_tests():
    with Session(engine) as session:
        query = select(Test)
        tests = []
        for t in session.scalars(query):
            tests.append([
                t.code,
                t.label,
                t.desc,
                t.tmlt_code,
                t.tmlt_name,
                t.specimens.label,
                t.method.method,
                t.unit,
                t.price,
                t.active,
            ])
    return tests


@login_required
def create_test_list_window():
    tests = load_all_tests()
    layout = [
        [sg.Table(values=tests,
                  headings=['Code', 'Label', 'Description',
                            'TMLT Code', 'TMLT Name', 'Specimens',
                            'Method', 'Unit', 'Price', 'Active'],
                  key='-TABLE-', expand_x=True, expand_y=True, enable_events=True)],
        [
            sg.CloseButton('Close'),
            sg.Button('Add TMLT Test', button_color=('white', 'green')),
            sg.Button('Add Custom Test', button_color=('white', 'green')),
        ],
    ]

    window = sg.Window('All Tests', layout=layout, modal=True, resizable=True, finalize=True)
    window['-TABLE-'].bind("<Double-Button-1>", " Double")

    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Add TMLT Test':
            create_tmlt_test_window()
            tests = load_all_tests()
            window.find_element('-TABLE-').update(values=tests)
            window.refresh()
        elif event == '-TABLE- Double':
            code = tests[values['-TABLE-'][0]][0]
            with Session(engine) as session:
                query = session.query(Test).filter(Test.code == code)
                test = session.scalar(query)
                create_tmlt_test_edit_form_window(test.to_dict())
                tests = load_all_tests()
                window.find_element('-TABLE-').update(values=tests)
                window.refresh()
        elif event == 'Add Custom Test':
            create_custom_test_form_window()
            tests = load_all_tests()
            window.find_element('-TABLE-').update(values=tests)
            window.refresh()
    window.close()


TMLT_ACCESS_TOKEN_URL = 'https://tmltconnect.this.or.th:7243/api/TmltToken/GetToken'
TMLT_SEARCH_URL = 'https://tmltconnect.this.or.th:7243/10686/search'
HOSPITAL_CODE = '10686'


@login_required
def create_tmlt_test_window():
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
                sg.popup_error('Error occurred. Could not fetch data from TMLT system.')
        elif event == '-TABLE- Double':
            try:
                row = values['-TABLE-'][0]
            except IndexError:
                pass
            else:
                create_tmlt_test_form_window(records[row])
    window.close()


@login_required
def create_tmlt_test_form_window(data):
    layout = [
        [sg.Text('Code', size=(16, 1)), sg.InputText(key='code')],
        [sg.Text('Label', size=(16, 1)), sg.InputText(key='label')],
        [sg.Text('Description', size=(16, 1)), sg.Multiline(data[1], size=(45, 5), key='desc')],
        [sg.Text('Price', size=(16, 1)), sg.InputText(key='price')],
        [sg.Text('TMLT Code', size=(16, 1)), sg.InputText(data[0], disabled=True, key='tmlt_code')],
        [sg.Text('TMLT Name', size=(16, 1)), sg.InputText(data[1], disabled=True, key='tmlt_name')],
        [sg.Text('Specimens', size=(16, 1)), sg.InputText(data[2], key='specimens')],
        [sg.Text('Component', size=(16, 1)), sg.InputText(data[12], key='component')],
        [sg.Text('Method', size=(16, 1)), sg.InputText(data[3], key='method')],
        [sg.Text('Unit', size=(16, 1)), sg.InputText(data[4], key='unit')],
        [sg.Text('CGD Code', size=(16, 1)), sg.InputText(data[5], disabled=True, key='cgd_code')],
        [sg.Text('CGD Name', size=(16, 1)), sg.InputText(data[6], key='cgd_name')],
        [sg.Text('CGD Price', size=(16, 1)), sg.InputText(data[7], key='cgd_price')],
        [sg.Text('Order Type', size=(16, 1)), sg.InputText(data[8], key='order_type')],
        [sg.Text('Scale', size=(16, 1)), sg.InputText(data[9], key='scale')],
        [sg.Text('LOINC Code', size=(16, 1)), sg.InputText(data[10], disabled=True, key='loinc_no')],
        [sg.Text('Panel', size=(16, 1)), sg.InputText(data[11], key='panel')],
        [sg.Text('Ref. Min', size=(16, 1)), sg.InputText(key='ref_min')],
        [sg.Text('Ref. Max', size=(16, 1)), sg.InputText(key='ref_max')],
        [sg.Text('Valuce Choices', size=(16, 1)), sg.Multiline(size=(45, 5), key='value_choices')],
        [sg.Button('Add', button_color=('white', 'green')), sg.CloseButton('Close', size=(8, 1))]
    ]

    window = sg.Window('Test Form', layout=layout, modal=True)

    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Add':
            new_test = Test(**values)
            with Session(engine) as session:
                session.add(new_test)
                session.commit()
                logger.info(f'ADD NEW TMLT TEST: {values["code"]}')
                sg.popup_ok(f"{new_test} added.")
                break
    window.close()


@login_required
def create_tmlt_test_edit_form_window(data):
    layout = [
        [sg.Text('Code', size=(16, 1)), sg.InputText(data['code'], key='code')],
        [sg.Text('Label', size=(16, 1)), sg.InputText(data['label'], key='label')],
        [sg.Text('Description', size=(16, 1)), sg.Multiline(data['desc'], size=(45, 5), key='desc')],
        [sg.Text('Price', size=(16, 1)), sg.InputText(data['price'], key='price')],
        [sg.Text('TMLT Code', size=(16, 1)), sg.InputText(data['tmlt_code'], disabled=True, key='tmlt_code')],
        [sg.Text('TMLT Name', size=(16, 1)), sg.InputText(data['tmlt_name'], disabled=True, key='tmlt_name')],
        [sg.Text('Specimens', size=(16, 1)), sg.InputText(data['specimens'], key='specimens')],
        [sg.Text('Component', size=(16, 1)), sg.InputText(data['component'], key='component')],
        [sg.Text('Method', size=(16, 1)), sg.InputText(data['method'], key='method')],
        [sg.Text('Unit', size=(16, 1)), sg.InputText(data['unit'], key='unit')],
        [sg.Text('CGD Code', size=(16, 1)), sg.InputText(data['cgd_code'], disabled=True, key='cgd_code')],
        [sg.Text('CGD Name', size=(16, 1)), sg.InputText(data['cgd_name'], key='cgd_name')],
        [sg.Text('CGD Price', size=(16, 1)), sg.InputText(data['cgd_price'], key='cgd_price')],
        [sg.Text('Order Type', size=(16, 1)), sg.InputText(data['order_type'], key='order_type')],
        [sg.Text('Scale', size=(16, 1)), sg.InputText(data['scale'], key='scale')],
        [sg.Text('LOINC Code', size=(16, 1)), sg.InputText(data['loinc_no'], disabled=True, key='loinc_no')],
        [sg.Text('Panel', size=(16, 1)), sg.InputText(data['panel'], key='panel')],
        [sg.Text('Ref. Min', size=(16, 1)), sg.InputText(data['ref_min'], key='ref_min')],
        [sg.Text('Ref. Max', size=(16, 1)), sg.InputText(data['ref_max'], key='ref_max')],
        [sg.Text('Valuce Choices', size=(16, 1)), sg.Multiline(data['value_choices'], size=(45, 5), key='value_choices')],
        [sg.Checkbox('Active', default=data['active'], key='active')],
        [sg.Button('Update', button_color=('white', 'green')), sg.CloseButton('Close', size=(8, 1))]
    ]

    window = sg.Window('Test Edit Form', layout=layout, modal=True)

    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Update':
            with Session(engine) as session:
                test = session.scalar(select(Test).where(Test.id == data['id']))
                test.update_from_dict(values, session)
                session.add(test)
                session.commit()
                logger.info(f'UPDATED TEST: {values["code"]}')
                sg.popup_ok(f"{test} data has been saved.")
                break
    window.close()


@login_required
def create_custom_test_form_window():
    layout = [
        [sg.Text('Code', size=(16, 1)), sg.InputText(key='code')],
        [sg.Text('Label', size=(16, 1)), sg.InputText(key='label')],
        [sg.Text('Description', size=(16, 1)), sg.Multiline(size=(45, 5), key='desc')],
        [sg.Text('Price', size=(16, 1)), sg.InputText(key='price')],
        [sg.Text('TMLT Code', size=(16, 1)), sg.InputText(key='tmlt_code')],
        [sg.Text('TMLT Name', size=(16, 1)), sg.InputText(key='tmlt_name')],
        [sg.Text('Specimens', size=(16, 1)), sg.InputText(key='specimens')],
        [sg.Text('Component', size=(16, 1)), sg.InputText( key='component')],
        [sg.Text('Method', size=(16, 1)), sg.InputText(key='method')],
        [sg.Text('Unit', size=(16, 1)), sg.InputText(key='unit')],
        [sg.Text('CGD Code', size=(16, 1)), sg.InputText(key='cgd_code')],
        [sg.Text('CGD Name', size=(16, 1)), sg.InputText(key='cgd_name')],
        [sg.Text('CGD Price', size=(16, 1)), sg.InputText(key='cgd_price')],
        [sg.Text('Order Type', size=(16, 1)), sg.InputText(key='order_type')],
        [sg.Text('Scale', size=(16, 1)), sg.InputText(key='scale')],
        [sg.Text('LOINC Code', size=(16, 1)), sg.InputText(key='loinc_no')],
        [sg.Text('Panel', size=(16, 1)), sg.InputText(key='panel')],
        [sg.Text('Ref. Min', size=(16, 1)), sg.InputText(key='ref_min')],
        [sg.Text('Ref. Max', size=(16, 1)), sg.InputText(key='ref_max')],
        [sg.Text('Valuce Choices', size=(16, 1)), sg.Multiline(size=(45, 5), key='value_choices')],
        [sg.Button('Add', button_color=('white', 'green')), sg.CloseButton('Close', size=(8, 1))]
    ]

    window = sg.Window('Custom Test Form', layout=layout, modal=True)

    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Add':
            new_test = Test(**values)
            with Session(engine) as session:
                session.add(new_test)
                session.commit()
                logger.info(f'ADD NEW CUSTOM TEST: {values["code"]}')
                sg.popup_ok(f"{new_test} added.")
                break
    window.close()


def format_datetime(isodatetime, datetime_format='%d/%m/%Y %H:%M:%S'):
    """A helper function that converts isodatetime to a datetime with a given format."""
    try:
        return datetime.fromisoformat(isodatetime).strftime(datetime_format)
    except:
        return isodatetime


@login_required
def create_order_list_window():
    def load_orders():
        data = []
        with Session(engine) as session:
            query = select(LabOrder)
            for order in session.scalars(query):
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
            # create_order_item_list_window(access_token, data[values['-ORDER-TABLE-'][0]][0])
            # data = load_orders()
            # window.find_element('-ORDER-TABLE-').update(values=data)
            pass
        elif event == '-GET-ORDER-':
            # TODO: add code to check if the simulations run successfully
            # resp = requests.get(f'http://127.0.0.1:5000/api/simulations', headers=headers)
            # resp = requests.get(f'http://127.0.0.1:5000/api/orders', headers=headers)
            # data = load_orders()
            # window.find_element('-ORDER-TABLE-').update(values=data)
            pass
    window.close()
