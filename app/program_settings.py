"""Application preferences independent of LIS database configuration."""
import FreeSimpleGUI as sg

from app.config import config_dict, update_config_yaml
from app.ui_styles import create_window


def simulation_menu_enabled():
    return bool(config_dict.get('simulation_enabled', True))


def create_program_settings_window(*, parent=None):
    """Save preferences; return True on save and False on cancel."""
    layout = [[sg.Text('Program Settings', font=('Arial', 18, 'bold'))],
              [sg.Checkbox('Show Simulation menu', default=simulation_menu_enabled(),
                           key='simulation_enabled')],
              [sg.Text('Includes laboratory design, workflows, scenarios, and simulation results.')],
              [sg.Button('Save'), sg.Button('Cancel')]]
    window = create_window('Program Settings', layout, modal=True, parent=parent)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, 'Cancel'):
                return False
            if event == 'Save':
                try:
                    update_config_yaml(simulation_enabled=bool(values['simulation_enabled']))
                except OSError as exc:
                    sg.popup_error(f'Could not save program settings: {exc}', keep_on_top=True)
                    continue
                return True
    finally:
        window.close()
