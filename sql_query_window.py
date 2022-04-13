import PySimpleGUI as sg
from sql_formatter.core import format_sql
from sqlalchemy import create_engine
import pandas as pd
from tabulate import tabulate

engine = create_engine('sqlite:///demo.db')

sg.theme('BlueMono')


def show_save_query_dialog():
    layout = [
        [sg.Input(key='-filepath-'), sg.FileSaveAs('Browse')],
        [sg.Ok()]
    ]
    dialog = sg.Window('Save As', layout)
    filepath = ''
    while True:
        event, values = dialog.read()
        if event in (sg.WIN_CLOSED, ):
            break
        elif event == 'Ok':
            filepath = values['-filepath-']
            break

    dialog.close()
    return filepath


layout = [
    [sg.Text('Results'),],
    [sg.Multiline(key='-table-', size=(80, 10), reroute_stdout=True,
                  font='Courier 13', horizontal_scroll=True)],
    [sg.Button('Save Data', key='-save-data-')],
    [sg.Text('SQL Query')],
    [sg.Multiline(key='-query-', size=(80, 10),
                  focus=True, font='Courier 13 bold',  text_color='blue')],
    [sg.Button('Run'), sg.Button('Format'), sg.Button('Save Query', key='-save-query-'),
     sg.Button('Clear'), sg.Button('Exit')],
    [sg.Text('Console')],
    [sg.Multiline(key='-console-', size=(80, 5), font='Courier 13 bold')]
]

window = sg.Window('Lab Tycoon Desktop v.0.1', layout=layout)

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