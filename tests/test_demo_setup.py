"""Demo database setup, validation, and cancellation."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app import demo_setup
from app.database import DatabaseConfig, DatabaseManager
from app.system.models import User, Test


class DemoSetupTests(unittest.TestCase):
    def test_new_database_is_initialized_and_stages_named_demo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'demo.db'
            with patch.object(demo_setup, 'update_config_yaml') as save:
                project, restart = demo_setup.prepare_demo(' My Lab ', path, create_new=True)
            self.assertEqual(project.name, 'My Lab')
            self.assertTrue(restart)
            self.assertEqual(save.call_args.kwargs['pending_demo']['name'], 'My Lab')
            target = DatabaseManager(DatabaseConfig.default_sqlite(path)).create_orm_engine()
            try:
                with Session(target) as session:
                    self.assertEqual(session.scalar(select(func.count()).select_from(User)), 0)
                    self.assertEqual(session.scalar(select(func.count()).select_from(Test)), 9)
            finally:
                target.dispose()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                demo_setup.prepare_demo('Other', path, create_new=True)
            with patch.object(demo_setup, 'DATABASE_CONFIG', DatabaseConfig.default_sqlite(path)), \
                 patch.object(demo_setup, 'update_config_yaml') as save:
                project, restart = demo_setup.prepare_demo('Same Database', path)
                self.assertFalse(restart)
                save.assert_not_called()

    def test_validation_does_not_change_settings(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(demo_setup, 'update_config_yaml') as save:
            path = Path(directory) / 'empty.db'
            DatabaseManager(DatabaseConfig.default_sqlite(path)).create_database()
            for name, file in (('', path), ('Lab', ''), ('Lab', path), ('Lab', path.with_name('missing.db'))):
                with self.subTest(name=name, file=file), self.assertRaises(ValueError):
                    demo_setup.prepare_demo(name, file)
            save.assert_not_called()

    def test_cancel_does_not_create_database(self):
        window = Mock()
        window.read.return_value = ('Cancel', {})
        with patch.object(demo_setup, 'create_window', return_value=window), \
             patch.object(demo_setup, 'prepare_demo') as prepare:
            self.assertIsNone(demo_setup.create_demo_setup_window())
            prepare.assert_not_called()
            window.close.assert_called_once()
