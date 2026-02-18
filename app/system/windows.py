import FreeSimpleGUI as sg
import pandas as pd
from sql_formatter.core import format_sql
from tabulate import tabulate

from app.auth.windows import login_required
from app.system.models import engine


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
