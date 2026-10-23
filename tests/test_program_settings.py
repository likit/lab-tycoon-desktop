"""Saved menu preference and cancellation behavior."""
import unittest
from unittest.mock import Mock, patch
from app.program_settings import create_program_settings_window, simulation_menu_enabled


class ProgramSettingsTests(unittest.TestCase):
    def test_existing_installations_show_simulation_by_default(self):
        with patch('app.program_settings.config_dict', {}):
            self.assertTrue(simulation_menu_enabled())
        with patch('app.program_settings.config_dict', {'simulation_enabled': False}):
            self.assertFalse(simulation_menu_enabled())

    def test_save_records_choice_and_cancel_does_not_write(self):
        for event, expected in [('Save', True), ('Cancel', False)]:
            with self.subTest(event=event):
                window = Mock()
                window.read.return_value = (event, {'simulation_enabled': False})
                with patch('app.program_settings.create_window', return_value=window), \
                     patch('app.program_settings.update_config_yaml') as save:
                    self.assertEqual(create_program_settings_window(), expected)
                if expected:
                    save.assert_called_once_with(simulation_enabled=False)
                else:
                    save.assert_not_called()
                window.close.assert_called_once()
