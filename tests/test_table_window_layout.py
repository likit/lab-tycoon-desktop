"""Wide table dialogs remain navigable on small logical desktops."""
import unittest
from unittest.mock import patch, Mock
import FreeSimpleGUI as sg
from app.ui_styles import table_window_layout


class TableWindowLayoutTests(unittest.TestCase):
    def test_nested_tables_get_screen_bounded_scrollable_viewport(self):
        for screen in ((1366, 768), (1024, 640), (1920, 1080)):
            with self.subTest(screen=screen):
                table = sg.Table(values=[['long' * 100]], headings=['Description'], num_rows=30)
                original = [[sg.TabGroup([[sg.Tab('Orders', [[table]])]])], [sg.Button('Close')]]
                with patch('app.ui_styles.sg.Column', return_value=Mock()) as column:
                    layout, options = table_window_layout(original, screen)
                self.assertLess(options['size'][0], screen[0])
                self.assertLess(options['size'][1], screen[1])
                self.assertEqual(options['location'][0] + options['size'][0], (screen[0] + options['size'][0]) // 2)
                self.assertEqual(table.NumRows, 12)
                self.assertFalse(table.VerticalScrollOnly)
                self.assertTrue(column.call_args.kwargs['scrollable'])
                self.assertFalse(column.call_args.kwargs['vertical_scroll_only'])
                self.assertIs(column.call_args.args[0], original)
                self.assertEqual(layout, [[column.return_value]])

    def test_short_tables_keep_their_row_count_and_other_layouts_are_unchanged(self):
        table = sg.Table(values=[], headings=['Test'], num_rows=4)
        with patch('app.ui_styles.sg.Column'):
            table_window_layout([[table]], (1024, 768))
        self.assertEqual(table.NumRows, 4)
        layout = [[sg.Text('Project name'), sg.Input()]]
        bounded, options = table_window_layout(layout, (1024, 768))
        self.assertIs(bounded, layout)
        self.assertEqual(options, {})
