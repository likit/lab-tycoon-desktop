"""Tests for the read-only database explorer infrastructure."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sqlalchemy import create_engine, text

from app.database_explorer import format_preview, inspect_database_table, list_database_tables


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


if __name__ == "__main__":
    unittest.main()
