"""First account setup and startup dialog behavior."""
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.auth import windows
from app.system.models import Base, User, UserRole


class InitialAdminTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.values = dict(username='owner', firstname='Lab', lastname='Owner',
                           password='chosen-password', confirm_password='chosen-password')

    def tearDown(self):
        self.engine.dispose()

    def test_creates_active_account_with_hashed_password_and_all_roles(self):
        with Session(self.engine) as session:
            session.add(UserRole(role_need='custom'))
            session.commit()
        self.assertTrue(windows.users_table_is_empty(self.engine))
        windows.create_initial_admin(self.values, self.engine)
        with Session(self.engine) as session:
            user = session.scalar(select(User))
            self.assertEqual(user.username, 'owner')
            self.assertTrue(user.active)
            self.assertTrue(user.check_password(b'chosen-password'))
            self.assertEqual({r.role_need for r in user.roles},
                             {'admin', 'approver', 'reporter', 'custom'})
        self.assertFalse(windows.users_table_is_empty(self.engine))
        with self.assertRaisesRegex(ValueError, 'already exists'):
            windows.create_initial_admin(self.values, self.engine)

    def test_invalid_input_does_not_create_an_account(self):
        for changes in ({'username': ' '}, {'firstname': ''}, {'lastname': ''},
                        {'password': ''}, {'confirm_password': 'different'},
                        {'password': 'a' * 73, 'confirm_password': 'a' * 73}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                windows.create_initial_admin(self.values | changes, self.engine)
            self.assertTrue(windows.users_table_is_empty(self.engine))

    def test_existing_users_skip_dialog(self):
        with patch.object(windows, 'users_table_is_empty', return_value=False), \
             patch.object(windows, 'create_window') as create:
            self.assertTrue(windows.create_initial_admin_window())
            create.assert_not_called()

    def test_dialog_cancel_and_validation_retry(self):
        for events, errors, expected in (
            ([('Exit', {})], [], False),
            ([('Create Account', {}), ('Create Account', self.values)],
             [ValueError('Required'), None], True),
        ):
            window = Mock()
            window.read.side_effect = events
            with patch.object(windows, 'users_table_is_empty', return_value=True), \
                 patch.object(windows, 'create_window', return_value=window), \
                 patch.object(windows, 'create_initial_admin', side_effect=errors) as save, \
                 patch.object(windows.sg, 'popup_error'):
                self.assertEqual(windows.create_initial_admin_window(), expected)
                self.assertEqual(save.call_count, len(errors))
                window.close.assert_called_once()
