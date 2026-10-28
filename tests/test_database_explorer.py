"""Tests for the read-only database explorer infrastructure."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import csv
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text

from app.database_explorer import export_database_table, format_preview, inspect_database_table, list_database_tables


class DatabaseExplorerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        path = Path(self.temporary_directory.name) / "explorer.db"
        self.engine = create_engine(f"sqlite:///{path}")
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE samples (id INTEGER PRIMARY KEY, label TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO samples (label) VALUES ('Alpha'), ('Beta')"))

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_lists_database_tables(self) -> None:
        self.assertEqual(list_database_tables(self.engine), ["samples"])

    def test_inspects_schema_and_previews_bounded_rows(self) -> None:
        schema, headings, rows = inspect_database_table(self.engine, "samples", preview_limit=1)

        self.assertEqual(headings, ["id", "label"])
        self.assertEqual(rows, [[1, "Alpha"]])
        self.assertEqual(schema[0][0], "id")
        self.assertEqual(schema[0][4], "Yes")

    def test_rejects_unknown_table_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown database table"):
            inspect_database_table(self.engine, "not_a_table")

    def test_formats_null_values_for_preview(self) -> None:
        preview = format_preview(["id", "value"], [[1, None]])

        self.assertIn("id\tvalue", preview)
        self.assertIn("1\tNULL", preview)

    def test_csv_exports_all_rows_and_preserves_unicode_quotes_and_line_breaks(self):
        label = 'ทดสอบ, "sample"\nsecond line'
        with self.engine.begin() as connection:
            connection.execute(text('INSERT INTO samples (label) VALUES (:label)'),
                               [dict(label=label)] * 105)
        destination = Path(self.temporary_directory.name) / 'samples.csv'
        self.assertEqual(export_database_table(self.engine, 'samples', destination), 107)
        with destination.open(encoding='utf-8-sig', newline='') as source:
            rows = list(csv.reader(source))
        self.assertEqual(rows[0], ['id', 'label'])
        self.assertEqual(len(rows), 108)
        self.assertEqual(rows[-1][1], label)

    def test_empty_table_exports_headers_and_null_exports_empty_field(self):
        with self.engine.begin() as connection:
            connection.execute(text('CREATE TABLE empty (id INTEGER, value TEXT)'))
        destination = Path(self.temporary_directory.name) / 'empty.csv'
        self.assertEqual(export_database_table(self.engine, 'empty', destination), 0)
        self.assertEqual(destination.read_text(encoding='utf-8-sig'), 'id,value\n')
        with self.engine.begin() as connection:
            connection.execute(text('INSERT INTO empty VALUES (1, NULL)'))
        self.assertEqual(export_database_table(self.engine, 'empty', destination), 1)
        self.assertIn('1,\n', destination.read_text(encoding='utf-8-sig'))

    def test_invalid_table_and_failed_export_preserve_existing_file(self):
        destination = Path(self.temporary_directory.name) / 'existing.csv'
        destination.write_text('keep me')
        with self.assertRaises(ValueError):
            export_database_table(self.engine, 'samples; DROP TABLE samples', destination)
        with patch('app.database_explorer.os.replace', side_effect=OSError('Cannot replace')):
            with self.assertRaises(OSError):
                export_database_table(self.engine, 'samples', destination)
        self.assertEqual(destination.read_text(), 'keep me')
        self.assertFalse(list(destination.parent.glob('.table-export-*')))

    def test_download_button_exports_selected_table_and_cancel_does_nothing(self):
        from app.database_explorer import create_database_explorer_window
        destination = Path(self.temporary_directory.name) / 'download.csv'
        window = Mock()
        window.__getitem__ = Mock(return_value=Mock())
        window.read.side_effect = [('-DBX-TREE-', {'-DBX-TREE-': ['table:samples']}),
                                  ('-DBX-DOWNLOAD-', {}), ('-DBX-DOWNLOAD-', {}), ('Close', {})]
        with patch('app.database_explorer.engine', self.engine), \
             patch('app.database_explorer.create_window', return_value=window), \
             patch('app.database_explorer.filedialog.asksaveasfilename',
                   side_effect=['', str(destination)]) as choose, \
             patch('app.database_explorer.bring_window_to_front') as focus, \
             patch('app.database_explorer.messagebox.showinfo') as success:
            create_database_explorer_window.__wrapped__()
        self.assertEqual(choose.call_args.kwargs['parent'], window.TKroot)
        self.assertEqual(choose.call_args.kwargs['initialfile'], 'samples.csv')
        self.assertEqual(focus.call_count, 2)
        self.assertTrue(destination.exists())
        success.assert_called_once()
        self.assertIn('Saved 2 rows', success.call_args.kwargs['message'])
        self.assertIs(success.call_args.kwargs['parent'], window.TKroot)
        window.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
