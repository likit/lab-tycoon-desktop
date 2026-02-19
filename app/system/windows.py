import random
import datetime

import FreeSimpleGUI as sg
import pandas as pd
import requests
import simpy
from sql_formatter.core import format_sql
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session
from tabulate import tabulate

from app.auth.windows import login_required, session_manager
from app.system.models import engine, Test, LabOrder, Customer, LabOrderItem, User
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
        [sg.Text('Value Choices', size=(16, 1)), sg.Multiline(size=(45, 5), key='value_choices')],
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
        [sg.Text('Value Choices', size=(16, 1)),
         sg.Multiline(data['value_choices'], size=(45, 5), key='value_choices')],
        [sg.Checkbox('Active', default=data['active'], key='active')],
        [sg.Button('Update', button_color=('white', 'green')), sg.CloseButton('Close', size=(8, 1))]
    ]

    window = sg.Window('Test Edit Form', layout=layout, modal=True)

    while True:
        event, values = window.read()
        if event in ['CloseButton', sg.WIN_CLOSED]:
            break
        elif event == 'Update':
            #TODO: add updater
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
        [sg.Text('Component', size=(16, 1)), sg.InputText(key='component')],
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


def format_datetime(dt, datetime_format='%d/%m/%Y %H:%M:%S'):
    """A helper function that converts isodatetime to a datetime with a given format."""
    if not dt:
        return None
    return dt.strftime(datetime_format)


@login_required
def create_order_list_window():
    def load_orders():
        data = []
        with Session(engine) as session:
            query = select(LabOrder)
            for order in session.scalars(query):
                if order.approved_at:
                    status = 'APPROVED'
                    status_datetime = format_datetime(order.approved_at)
                elif order.rejected_at:
                    status = 'REJECTED'
                    status_datetime = format_datetime(order.rejected_at)
                elif order.cancelled_at:
                    status = 'CANCELLED'
                    status_datetime = format_datetime(order.cancelled_at)
                elif order.received_at:
                    status = 'RECEIVED'
                    status_datetime = format_datetime(order.received_at)
                else:
                    status = 'PENDING'
                    status_datetime = ''

                data.append([
                    order.id,
                    order.customer.hn,
                    order.customer.fullname,
                    format_datetime(order.order_datetime) or '',
                    status,
                    status_datetime,
                    len(order.order_items),
                ])
        return data

    data = load_orders()

    layout = [
        [sg.Table(values=data, headings=['ID', 'HN', 'Customer', 'Ordered At',
                                         'Status', 'Time',
                                         'Items'],
                  key="-ORDER-TABLE-", auto_size_columns=True,
                  alternating_row_color='lightblue',
                  font=('Arial', 16),
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
            create_order_item_list_window(data[values['-ORDER-TABLE-'][0]][0])
            data = load_orders()
            window.find_element('-ORDER-TABLE-').update(values=data)
        elif event == '-GET-ORDER-':
            # TODO: add code to check if the simulations run successfully
            with Session(engine) as session:
                query = select(Customer).order_by(func.random())
                customer = session.scalar(query)
                tests = session.scalars(select(Test)).all()
                if len(tests) == 0:
                    sg.popup_ok(f"Add some tests first.")
                    break
                order = LabOrder(customer=customer,
                                 order_datetime=datetime.datetime.now())
                n = random.randint(1, len(tests))
                ordered_items = set()
                for test in random.choices(tests, k=n):
                    if test not in ordered_items:
                        order_item = LabOrderItem(test=test)
                        order.order_items.append(order_item)
                        ordered_items.add(test)

                # order.received_at = order.order_datetime + datetime.timedelta(minutes=mins)
                # logger.info(f'LAB ORDER ID={order.id} RECEIVED AT {order.received_at}')
                session.add(order)
                session.commit()
            data = load_orders()
            window.find_element('-ORDER-TABLE-').update(values=data)
            window.refresh()
    window.close()


@login_required
def create_order_item_list_window(lab_order_id):
    def load_item_list():
        with Session(engine) as session:
            query = select(LabOrder).where(LabOrder.id == lab_order_id)
            order = session.scalar(query)
            items = []
            for item in order.order_items:
                items.append([
                    item.id,
                    item.test.code,
                    item.test.tmlt_name,
                    item.value_string or '',
                    format_datetime(item.reported_at) or '',
                    item.reporter or '',
                    format_datetime(item.approved_at) or '',
                    item.approver or '',
                    format_datetime(item.finished_at) or '',
                    format_datetime(item.cancelled_at) or '',
                ])
            return items

    items = load_item_list()
    with Session(engine) as session:
        query = select(LabOrder).where(LabOrder.id == lab_order_id)
        order = session.scalar(query)

        is_order_rejected = order.rejected_at is not None
        is_order_cancelled = order.cancelled_at is not None
        is_order_received = order.received_at is not None
        is_order_approved = order.approved_at is not None

        desc = """
        The table displays a list of items in the order. Click the Reject button to reject the order or click the Cancel button to cancel the order.
        """
        layout = [
            [sg.Text('HN:'), sg.Text(order.customer.hn),
             sg.Text('Name:'), sg.Text(f"{order.customer.fullname}"),
             sg.Text('Ordered At:'), sg.Text(f"{format_datetime(order.order_datetime)}"),
             ],
            [sg.Table(values=items, headings=['Item ID', 'Code', 'Name', 'Result', 'Reported At',
                                              'Reporter', 'Approved At', 'Approver', 'Finished At', 'Cancelled At'],
                      key="-ORDER-ITEM-TABLE-",
                      auto_size_columns=True,
                      alternating_row_color='lightgrey',
                      font=('Arial', 16),
                      expand_y=True,
                      expand_x=True,
                      enable_events=True,
                      )
             ],
            [
                sg.Button('Accept', button_color=('white', 'green'),
                          disabled_button_color=('white', 'lightgrey'),
                          disabled=is_order_received or is_order_rejected or is_order_cancelled),
                sg.Button('Approve', button_color=('white', 'green'),
                          disabled_button_color=('white', 'lightgrey'),
                          disabled=not is_order_received or is_order_rejected or is_order_cancelled or is_order_approved),
                sg.Button('Reject', button_color=('white', 'red'),
                       disabled_button_color=('white', 'lightgrey'),
                       disabled=is_order_approved or is_order_rejected or is_order_cancelled),
                sg.Button('Cancel', button_color=('white', 'red'),
                       disabled_button_color=('white', 'lightgrey'),
                       disabled=is_order_cancelled or is_order_rejected or is_order_approved),
                sg.CloseButton('Close')
            ],
            [sg.Text(f'RECEIVED at {format_datetime(order.received_at)} by {order.receiver}',
                     key='receive-banner', pad=(5, 5), visible=False)],
            [sg.Text(f'APPROVED at {format_datetime(order.approved_at)} by {order.approver}',
                     background_color='green', text_color='white', pad=(5, 5), key='approve-banner', visible=False)],
            [sg.Text(f'REJECTED at {format_datetime(order.rejected_at)}: {order.reason}',
                     background_color='red', text_color='white', pad=(5,5), key='reject-banner', visible=False)],
        ]

        window = sg.Window('Ordered Item List', layout=layout, modal=True, finalize=True, resizable=True)
        window['-ORDER-ITEM-TABLE-'].bind("<Double-Button-1>", " Double")
        if order.rejected_at:
            window['reject-banner'].update(visible=True)
        elif order.approved_at:
            window['approve-banner'].update(visible=True)
        elif order.received_at:
            window['receive-banner'].update(visible=True)
    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED):
            break
        elif event == '-ORDER-ITEM-TABLE- Double' and values['-ORDER-ITEM-TABLE-']:
            create_item_detail_window(items[values['-ORDER-ITEM-TABLE-'][0]][0])
            items = load_item_list()
            window.find_element('-ORDER-ITEM-TABLE-').update(values=items)
            window.refresh()
        elif event == 'Reject':
            resp = sg.popup_ok_cancel('Are you sure want to reject this order?', title='Order Rejection')
            if resp == 'OK':
                with Session(engine) as session:
                    current_user = session.scalar(select(User).where(User.username == session_manager.current_user))
                    order = session.scalar(select(LabOrder).where(LabOrder.id == lab_order_id))
                    order.rejected_at = datetime.datetime.now()
                    order.rejecter = current_user
                    order.reason, order.comment = create_reject_reason_window()
                    session.add(order)
                    session.commit()
                    sg.popup_ok('Order has been rejected.')
                    window.find_element('Reject').update(disabled=True)
                    window.find_element('Approve').update(disabled=True)
                    window.find_element('Accept').update(disabled=True)
                    window.find_element('Cancel').update(disabled=True)
                    window.find_element('reject-banner').update(f'REJECTED at {format_datetime(order.rejected_at)} by {order.rejector}',
                                                                visible=True)
                    window.find_element('approve-banner').update(visible=False)
                    window.find_element('receive-banner').update(visible=False)
        elif event == 'Accept':
            resp = sg.popup_ok_cancel('Are you sure want to accept this order?', title='Order Rejection')
            if resp == 'OK':
                with Session(engine) as session:
                    current_user = session.scalar(select(User).where(User.username == session_manager.current_user))
                    order = session.scalar(select(LabOrder).where(LabOrder.id == lab_order_id))
                    order.received_at = datetime.datetime.now()
                    order.receiver = current_user
                    session.add(order)
                    session.commit()
                    sg.popup_ok('Order has been received.')
                    window.find_element('Accept').update(disabled=True)
                    window.find_element('reject-banner').update(visible=False)
                    window.find_element('approve-banner').update(visible=False)
                    window.find_element('receive-banner').update(f'RECEIVED at {format_datetime(order.received_at)} by {order.receiver}',
                                                                 visible=True)
        elif event == 'Approve':
            resp = sg.popup_ok_cancel('Are you sure want to approve this order?', title='Order Rejection')
            if resp == 'OK':
                with Session(engine) as session:
                    current_user = session.scalar(select(User).where(User.username == session_manager.current_user))
                    order = session.scalar(select(LabOrder).where(LabOrder.id == lab_order_id))
                    proceed = True
                    for item in order.order_items:
                        if not item.reported_at:
                            resp = sg.popup_ok_cancel('Some tests have not been reported. Do you want to proceed?', title='Order Approval')
                            if resp != 'OK':
                                proceed = False
                            break
                    if proceed:
                        order.approved_at = datetime.datetime.now()
                        order.approver = current_user
                        for item in order.order_items:
                            item.approved_at = order.approved_at
                            item.approver = current_user
                            session.add(item)
                        session.add(order)
                        session.commit()
                        sg.popup_ok('Order has been approved.')
                        items = load_item_list()
                        window.find_element('-ORDER-ITEM-TABLE-').update(values=items)
                        window.find_element('Reject').update(disabled=True)
                        window.find_element('Approve').update(disabled=True)
                        window.find_element('Accept').update(disabled=True)
                        window.find_element('Cancel').update(disabled=True)
                        window.find_element('reject-banner').update(visible=False)
                        window.find_element('approve-banner').update(f'APPROVED at {format_datetime(order.approved_at)} by {order.approver}',
                                                                     visible=True)
                        window.find_element('receive-banner').update(visible=False)
        elif event == 'Cancel':
            resp = sg.popup_ok_cancel('Are you sure want to cancel this order?', title='Order Cancellation')
            if resp == 'OK':
                with Session(engine) as session:
                    current_user = session.scalar(select(User).where(User.username == session_manager.current_user))
                    order = session.scalar(select(LabOrder).where(LabOrder.id == lab_order_id))
                    order.cencelled_at = datetime.datetime.now()
                    order.canceller = current_user
                    session.add(order)
                    session.commit()
                    sg.popup_ok('Order has been cancelled.')
                break
    window.close()


@login_required
def create_item_detail_window(item_id):
    with Session(engine) as session:
        item = session.scalar(select(LabOrderItem).where(LabOrderItem.id == item_id))
        actions = [
            sg.Button('Approve',
                      button_color=('white', 'green'),
                      disabled_button_color=('white', 'lightgrey'),
                      disabled=item.approved_at is not None),
            sg.Button('Report',
                      button_color=('white', 'green'),
                      disabled_button_color=('white', 'lightgrey'),
                      disabled=item.reported_at is not None),
            sg.Button('Update', button_color=('white', 'green')),
            sg.Button('Cancel', button_color=('white', 'red'),
                      disabled_button_color=('white', 'lightgrey'),
                      disabled=item.cancelled_at is not None),
        ]

        if item.cancelled_at:
            is_item_cancelled = True
            actions = []
        else:
            is_item_cancelled = False

        layout = [
            [sg.Text('ID: ', size=(8, 1), font=('Arial', 16, 'bold')), sg.Text(item_id, font=('Arial', 16, 'bold')),
             sg.Text('Code: ', size=(8, 1), font=('Arial', 16, 'bold')), sg.Text(item.test.code, font=('Arial', 16, 'bold')),
             sg.Text('HN: ', font=('Arial', 16, 'bold')), sg.Text(item.order.customer.hn, font=('Arial', 16, 'bold')),
             sg.Text('Customer: ', font=('Arial', 16, 'bold')), sg.Text(item.order.customer.fullname, font=('Arial', 16, 'bold')),
             ],
            [sg.Text('Value'), sg.Input(item.value, key='-ITEM-VALUE-', disabled=is_item_cancelled)],
            [sg.Text('Comment')],
            [sg.Multiline(item.comment, key='-UPDATE-COMMENT-', size=(45, 10), disabled=is_item_cancelled)],
            actions,
            [sg.Button('Audit Trail'), sg.CloseButton('Close', button_color=('white', 'red'))],
        ]
    window = sg.Window('Lab Order Item Detail', layout=layout, modal=True, resizable=True)
    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED):
            break
        elif event == 'Cancel':
            response = sg.popup_ok_cancel('Are you sure want to cancel this item?')
            if response == 'OK':
                with Session(engine) as session:
                    current_user = session.scalar(select(User).where(User.username == session_manager.current_user))
                    item = session.scalar(select(LabOrderItem).where(LabOrderItem.id == item_id))
                    item.cancelled_at = datetime.datetime.now()
                    item.canceller = current_user
                    session.add(item)
                    session.commit()
                break
        elif event == 'Report':
            with Session(engine) as session:
                current_user = session.scalar(select(User).where(User.username == session_manager.current_user))
                if current_user.has_role('reporter'):
                    response = sg.popup_ok_cancel('Are you sure want to report this item?')
                    if response == 'OK':
                        item = session.scalar(select(LabOrderItem).where(LabOrderItem.id == item_id))
                        item.reported_at = datetime.datetime.now()
                        item.reporter = current_user
                        session.add(item)
                        session.commit()
                else:
                    sg.popup_error(f'{current_user.username} has no permission to report.')
                break
        elif event == 'Approve':
            with Session(engine) as session:
                current_user = session.scalar(select(User).where(User.username == session_manager.current_user))
                if current_user.has_role('approver'):
                    response = sg.popup_ok_cancel('Are you sure want to approve this item?')
                    if response == 'OK':
                        item = session.scalar(select(LabOrderItem).where(LabOrderItem.id == item_id))
                        item.approved_at = datetime.datetime.now()
                        item.approver = current_user
                        session.add(item)
                        session.commit()
                        window.find_element('Approve').update(disabled=True)
                else:
                    sg.popup_error(f'{current_user.username} has no permission to approve.')
                break
        elif event == 'Update':
            response = sg.popup_ok_cancel('Are you sure want to update this item?')
            if response == 'OK':
                with Session(engine) as session:
                    item = session.scalar(select(LabOrderItem).where(LabOrderItem.id == item_id))
                    item._value = values['-ITEM-VALUE-']
                    item.comment = values['-UPDATE-COMMENT-']
                    if not item.update_at:
                        item.finished_at = datetime.datetime.now()
                    item.updated_at = datetime.datetime.now()
                    item.approved_at = None
                    session.add(item)
                    session.commit()
                    sg.popup_ok('Results have been updated.')
                    window.find_element('Approve').update(disabled=False)
                break
        elif event == 'Audit Trail':
            create_lab_order_item_version_list_window(item_id)
    window.close()


@login_required
def create_customer_list_window():
    data = []
    with Session(engine) as session:
        for customer in session.scalars(select(Customer)):
            data.append([
                customer.id, customer.hn, customer.fullname, customer.dob
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
            create_customer_order_list_window(customer_id)
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


@login_required
def create_customer_order_list_window(customer_id):
    with Session(engine) as session:
        query = select(Customer).where(Customer.id == customer_id)
        customer = session.scalar(query)
        treedata = sg.TreeData()
        for order in customer.orders:
            treedata.insert('',
                            f"Order-{order.id}",
                            f"Order:{order.id}",
                            [format_datetime(order.received_at)])
            for item in order.order_items:
                treedata.insert(f"Order-{order.id}", item.id, f"Item:{item.id}",
                                ['', item.test.code, item.test.label, item.value,
                                 format_datetime(item.finished_at),
                                 format_datetime(item.reported_at),
                                 item.reporter,
                                 item.approved_at,
                                 item.approver,
                                 ])
        layout = [
            [sg.Tree(data=treedata, headings=['Received At', 'Code', 'Label', 'Value',
                                              'Finished At', 'Reported At', 'Reporter',
                                              'Approved At', 'Approver'],
                     auto_size_columns=True, show_expanded=True, expand_y=True, expand_x=True)],
            [sg.CloseButton('Close')]
        ]

        window = sg.Window('Customer Orders', layout=layout, resizable=True, modal=True, finalize=True)
        while True:
            event, values = window.read()
            if event in ['Exit', sg.WIN_CLOSED]:
                break
        window.close()


@login_required
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


@login_required
def create_lab_order_item_version_list_window(item_id):
    versions = []
    with Session(engine) as session:
        item = session.scalar(select(LabOrderItem).where(LabOrderItem.id == item_id))
        for n, ver in enumerate(item.versions, start=1):
            if ver._value:
                value = ver._value if ver.test.scale != 'Quantitative' else float(ver._value),
            else:
                value = None
            versions.append([
                n,
                value or '',
                format_datetime(ver.reported_at) or '',
                ver.reporter or '',
                format_datetime(ver.approved_at) or '',
                ver.approver or '',
                ver.comment,
                format_datetime(ver.updated_at) or '',
                ver.updater or '',
            ])

        layout = [
            [sg.Text('ID: '), sg.Text(item.id),
             sg.Text('Label: '), sg.Text(item.test.label),
             sg.Text('HN: '), sg.Text(item.order.customer.hn),
             sg.Text('Patient: '), sg.Text(item.order.customer.fullname),
             ],
            [sg.Table(values=versions, headings=['Version', 'Value', 'Reported At',
                                                 'Reporter', 'Approved At', 'Approver', 'Comment',
                                                 'Updated At', 'Updater'],
                      key="-VERSION-TABLE-",
                      auto_size_columns=True,
                      alternating_row_color='lightyellow',
                      font=('Arial', 16),
                      enable_events=True)
             ],
            [sg.CloseButton('Close')]
        ]
        window = sg.Window('Lab Order Item Detail', layout=layout, modal=True, resizable=True)
    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED):
            break
    window.close()


@login_required
def create_analysis_window():
    items = []
    with Session(engine) as session:
        query = select(LabOrderItem).where(and_(
            LabOrderItem.finished_at == None,
            LabOrderItem.cancelled_at == None)
        )
        for item in session.scalars(query):
            if item.order.received_at:
                items.append([
                    item.id,
                    item.test.code,
                    item.test.label,
                    item.test.tmlt_name,
                    format_datetime(item.order.received_at) or '',
                    item.order.customer.hn,
                    item.order.customer.fullname,
                ])
    layout = [
        [sg.Table(headings=['ID', 'Code', 'Label', 'TMLT Name', 'Received At', 'HN', 'Patient'],
                  values=items,
                  alternating_row_color='lightgrey',
                  auto_size_columns=True,
                  font=('Arial', 16),
                  key='-TABLE-',
                  enable_events=True)],
        [sg.Text('Analysis Log', font=('Arial', 16, 'bold'))],
        [sg.Output(key='-OUTPUT-',size=(75, 15))],
        [sg.Button('Run', button_color=('white', 'green')),
         sg.CloseButton('Close'),
         sg.Help()],
    ]

    window = sg.Window('Analysis', layout=layout, modal=True, resizable=True)

    while True:
        event, values = window.read()
        if event in ('Exit', sg.WIN_CLOSED):
            break
        elif event == 'Run':
            with Session(engine) as session:
                query = select(LabOrderItem).where(and_(
                    LabOrderItem.finished_at == None,
                    LabOrderItem.cancelled_at == None)
                )

                start_time = datetime.datetime.now()

                env = simpy.rt.RealtimeEnvironment(factor=0.1, strict=False)
                instrument = simpy.Resource(env, capacity=1)
                records = {}
                for item in session.scalars(query):
                    if item.order.received_at:
                        env.process(run_test(env, item, instrument, 5, 10, records))
                # staff = simpy.Resource(env, capacity=staff_count)
                env.run()
                for item, t in records.items():
                    item.random_value()
                    finished_at = start_time + datetime.timedelta(minutes=t)
                    item.finished_at = finished_at
                    item.updated_at = finished_at
                    session.add(item)
                session.commit()

        elif event == 'Help':
            sg.popup_ok('The list shows all test that waiting to be analyzed.'
                        ' If you click run, all tests will be sent to virtual analyzers.')
    window.close()

def print_stats(res):
    print(f'{res.count} of {res.capacity} slots are allocated.')
    print(f'  Users: {res.users}')
    print(f'  Queued events: {res.queue}')

def run_test(env, item, instrument, min_duration, max_duration, records):
    with instrument.request() as req:
        print(f'ID={item.id} {item.test.code} waiting to be analyzed...')
        yield req
        print(f'Analyzing ID={item.id} {item.test.code}...')
        yield env.timeout(random.randint(min_duration, max_duration))
        print(f'ID={item.id} {item.test.code} Done.')
        # print(print_stats(instrument))
    records[item] = env.now