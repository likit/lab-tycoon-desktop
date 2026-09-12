"""Tests for application-level LIS database configuration."""

from __future__ import annotations

from pathlib import Path
from contextlib import redirect_stdout
import io
import tempfile
import unittest
import warnings

from app.database import DatabaseConfig, DatabaseConfigurationError, DatabaseManager
from app.system.models import initialize_db
from sqlalchemy import inspect
from sqlalchemy.exc import SAWarning


class DatabaseConfigTests(unittest.TestCase):
    def test_default_configuration_uses_sqlite(self) -> None:
        config = DatabaseConfig.default_sqlite("example.db")

        self.assertEqual(config.driver, "sqlite")
        self.assertTrue(config.database_path.endswith("example.db"))

    def test_configuration_serialization_round_trip(self) -> None:
        original = DatabaseConfig.default_sqlite("example.db")

        restored = DatabaseConfig.from_dict(
            original.to_dict(),
            default_database_path="fallback.db",
        )

        self.assertEqual(restored, original)

    def test_missing_saved_configuration_uses_existing_default_path(self) -> None:
        default_path = Path("existing-default.db").resolve()

        config = DatabaseConfig.from_dict(None, default_database_path=default_path)

        self.assertEqual(config, DatabaseConfig.default_sqlite(default_path))


class DatabaseManagerTests(unittest.TestCase):
    def test_creates_a_new_sqlite_database_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new-lis.db"
            manager = DatabaseManager(DatabaseConfig.default_sqlite(path))

            self.assertEqual(manager.create_database(), path.resolve())
            self.assertTrue(path.is_file())
            manager.test_connection()

            with self.assertRaisesRegex(DatabaseConfigurationError, "already exists"):
                manager.create_database()

    def test_new_database_can_be_initialized_immediately_without_orm_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = DatabaseManager(
                DatabaseConfig.default_sqlite(Path(directory) / "initialized.db")
            )
            manager.create_database()
            target_engine = manager.create_orm_engine()
            try:
                with warnings.catch_warnings(), redirect_stdout(io.StringIO()):
                    warnings.simplefilter("error", SAWarning)
                    initialize_db(target_engine)
                inspector = inspect(target_engine)
                self.assertTrue(inspector.has_table("users"))
                self.assertTrue(inspector.has_table("lab_orders"))
            finally:
                target_engine.dispose()

    def test_opens_sqlite_connection_and_executes_select_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lis.db"
            manager = DatabaseManager(DatabaseConfig.default_sqlite(path))
            connection = manager.connect()
            try:
                self.assertEqual(connection.execute("SELECT 1").fetchone(), (1,))
            finally:
                connection.close()

            manager.test_connection()

    def test_connection_test_rejects_missing_database_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = DatabaseManager(
                DatabaseConfig.default_sqlite(Path(directory) / "missing.db")
            )

            with self.assertRaisesRegex(DatabaseConfigurationError, "does not exist"):
                manager.test_connection()

    def test_invalid_driver_is_rejected(self) -> None:
        manager = DatabaseManager(DatabaseConfig("postgresql", "/tmp/lis.db"))

        with self.assertRaisesRegex(DatabaseConfigurationError, "Unsupported"):
            manager.connect()

    def test_invalid_parent_directory_is_rejected(self) -> None:
        manager = DatabaseManager(
            DatabaseConfig.default_sqlite("/definitely/not/a/real/directory/lis.db")
        )

        with self.assertRaisesRegex(DatabaseConfigurationError, "directory does not exist"):
            manager.connect()


if __name__ == "__main__":
    unittest.main()
