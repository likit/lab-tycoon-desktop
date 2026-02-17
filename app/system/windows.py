import FreeSimpleGUI as sg

from app.auth.windows import login_required


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
