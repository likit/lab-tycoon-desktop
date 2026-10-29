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
                    active_codes = sorted(session.scalars(select(Test.code).where(Test.active.is_(True))))
                analyzer = next(node for node in project.workflow.graph.iter_nodes()
                                if node.metadata.get('equipment_id') == 'CHEM-001')
                self.assertEqual(analyzer.widget.metadata['analyzer_tests'], active_codes)
                self.assertEqual(save.call_args.kwargs['pending_demo']['analyzer_tests'], active_codes)
            finally:
                target.dispose()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                demo_setup.prepare_demo('Other', path, create_new=True)
            with patch.object(demo_setup, 'DATABASE_CONFIG', DatabaseConfig.default_sqlite(path)), \
                 patch.object(demo_setup, 'update_config_yaml') as save:
                project, restart = demo_setup.prepare_demo('Same Database', path)
                self.assertFalse(restart)
                save.assert_not_called()

    def test_new_blank_project_preserves_description_through_database_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'project.db'
            with patch.object(demo_setup, 'update_config_yaml') as save:
                project, restart = demo_setup.prepare_project(
                    'Teaching Lab', path, create_new=True, description='My laboratory')
            self.assertTrue(restart)
            self.assertEqual(project.name, 'Teaching Lab')
            self.assertEqual(project.description, 'My laboratory')
            self.assertEqual(list(project.workflow.graph.iter_nodes()), [])
            self.assertEqual(save.call_args.kwargs['pending_project'],
                             {'name': 'Teaching Lab', 'description': 'My laboratory'})
            self.assertIsNone(save.call_args.kwargs['pending_demo'])
            with patch.object(demo_setup, 'DATABASE_CONFIG', DatabaseConfig.default_sqlite(path)), \
                 patch.object(demo_setup, 'update_config_yaml') as save:
                _, restart = demo_setup.prepare_project('Another Lab', path)
                self.assertFalse(restart)
                save.assert_not_called()

    def test_new_project_dialog_submits_database_and_description(self):
        window = Mock()
        window.read.return_value = ('Create', dict(name='Lab', path='/tmp/new.db',
                                                   new=True, description='Example'))
        with patch.object(demo_setup, 'create_window', return_value=window), \
             patch.object(demo_setup, 'prepare_project', return_value=('project', True)) as prepare:
            self.assertEqual(demo_setup.create_project_setup_window(), ('project', True))
            prepare.assert_called_once_with('Lab', '/tmp/new.db', create_new=True, description='Example')
            window.close.assert_called_once()

    def test_validation_does_not_change_settings(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(demo_setup, 'update_config_yaml') as save:
            path = Path(directory) / 'empty.db'
            DatabaseManager(DatabaseConfig.default_sqlite(path)).create_database()
            for name, file in (('', path), ('Lab', ''), ('Lab', path), ('Lab', path.with_name('missing.db'))):
                with self.subTest(name=name, file=file), self.assertRaises(ValueError):
                    demo_setup.prepare_demo(name, file)
            save.assert_not_called()

    def test_ready_dialog_has_explicit_owner_and_closes_on_ok_or_window_close(self):
        for event in ('OK', demo_setup.sg.WIN_CLOSED):
            dialog, parent = Mock(), Mock()
            dialog.read.return_value = (event, {})
            with patch.object(demo_setup, 'create_window', return_value=dialog) as create:
                demo_setup.show_demo_ready(Mock(name='project'), parent=parent)
            self.assertIs(create.call_args.kwargs['parent'], parent)
            self.assertTrue(create.call_args.kwargs['modal'])
            dialog.close.assert_called_once()

    def test_cancel_does_not_create_database(self):
        window = Mock()
        window.read.return_value = ('Cancel', {})
        with patch.object(demo_setup, 'create_window', return_value=window), \
             patch.object(demo_setup, 'prepare_demo') as prepare:
            self.assertIsNone(demo_setup.create_demo_setup_window())
            prepare.assert_not_called()
            window.close.assert_called_once()
