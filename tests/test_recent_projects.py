"""Recent-project ordering, migration, and menu accessibility."""
from pathlib import Path
import unittest

from app.main_ui import build_main_menu, project_action_allowed
from app.recent_projects import (recent_project_paths, recent_project_index,
                                 recent_project_menu, CLEAR_RECENT_PROJECTS)


class RecentProjectsTests(unittest.TestCase):
    def test_history_is_normalized_deduplicated_and_bounded(self):
        paths = [str(Path('/tmp') / f'project-{index}.ltp') for index in range(12)]
        settings = {'recent_project_paths': paths + paths}
        result = recent_project_paths(settings, latest=paths[3])
        self.assertEqual(len(result), 10)
        self.assertEqual(result[0], str(Path(paths[3]).resolve()))
        self.assertEqual(len(set(result)), 10)
        self.assertEqual(settings['recent_project_paths'], paths + paths)

    def test_previous_last_project_is_used_until_history_is_explicit(self):
        settings = {'last_project_path': '/tmp/previous.ltp'}
        self.assertEqual(recent_project_paths(settings), [str(Path('/tmp/previous.ltp').resolve())])
        settings['recent_project_paths'] = []
        self.assertEqual(recent_project_paths(settings), [])

    def test_bad_history_values_are_ignored(self):
        self.assertEqual(recent_project_paths({'recent_project_paths': 'invalid'}), [])
        self.assertEqual(recent_project_paths({'recent_project_paths': [None, 5, ' ']}), [])

    def test_recent_projects_are_enabled_without_an_active_project(self):
        paths = ['/tmp/a/Demo.ltp', '/tmp/b/Demo.ltp']
        menu = dict(build_main_menu(has_active_project=False, recent_projects=paths))['File']
        submenu = menu[menu.index('Recent Projects') + 1]
        self.assertEqual(submenu, recent_project_menu(paths))
        for index, entry in enumerate(submenu[:2]):
            self.assertTrue(project_action_allowed(entry, False))
            self.assertEqual(recent_project_index(entry), index)
            self.assertIn(paths[index], entry)
        self.assertTrue(project_action_allowed(CLEAR_RECENT_PROJECTS, False))
        self.assertIsNone(recent_project_index('::recent-project:-1'))
        self.assertIsNone(recent_project_index('::recent-project:10'))
        self.assertEqual(recent_project_menu([]), ['!No recent projects'])
