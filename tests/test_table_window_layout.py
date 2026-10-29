"""Wide table dialogs remain navigable on small logical desktops."""
import unittest
from unittest.mock import patch, Mock
import FreeSimpleGUI as sg
from app.ui_styles import table_window_layout, pin_table_controls


class TableWindowLayoutTests(unittest.TestCase):
    def test_nested_tables_are_bounded_without_scrolling_controls(self):
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
                column.assert_not_called()
                self.assertIs(layout, original)

    def test_footer_space_is_reserved_and_columns_scroll_instead_of_compressing(self):
        table = sg.Table(values=[], headings=['Test'], num_rows=8)
        table.ParentRowFrame = Mock()
        table.TKTreeview = {'columns': ['Test']}
        tree = Mock()
        tree.__getitem__ = Mock(return_value=['Test'])
        table.TKTreeview = tree
        close = sg.Button('Close')
        close.ParentRowFrame = Mock()
        pin_table_controls([[table], [close]])
        close.ParentRowFrame.pack_configure.assert_called_once_with(side='bottom', before=table.ParentRowFrame)
        tree.column.assert_called_once_with('Test', stretch=False)


    def test_short_tables_keep_their_row_count_and_other_layouts_are_unchanged(self):
        table = sg.Table(values=[], headings=['Test'], num_rows=4)
        with patch('app.ui_styles.sg.Column'):
            table_window_layout([[table]], (1024, 768))
        self.assertEqual(table.NumRows, 4)
        layout = [[sg.Text('Project name'), sg.Input()]]
        bounded, options = table_window_layout(layout, (1024, 768))
        self.assertIs(bounded, layout)
        self.assertEqual(options, {})
