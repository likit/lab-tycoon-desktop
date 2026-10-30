"""Bounded project history and stable menu event keys."""
import os
from pathlib import Path

MAX_RECENT_PROJECTS = 10
RECENT_PROJECT_KEY = '::recent-project:'
CLEAR_RECENT_PROJECTS = 'Clear Recent Projects'


def recent_project_paths(settings, latest=None):
    """Normalize and deduplicate paths, keeping the most recent first."""
    stored = settings.get('recent_project_paths', [])
    if not isinstance(stored, list):
        stored = []
    candidates = ([latest] if latest else []) + stored
    if 'recent_project_paths' not in settings and settings.get('last_project_path'):
        candidates.append(settings['last_project_path'])
    paths, seen = [], set()
    for value in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        path = str(Path(value).expanduser().resolve())
        identity = os.path.normcase(path)
        if identity not in seen:
            paths.append(path)
            seen.add(identity)
        if len(paths) == MAX_RECENT_PROJECTS:
            break
    return paths


def recent_project_index(event):
    if isinstance(event, str) and RECENT_PROJECT_KEY in event:
        suffix = event.rsplit(RECENT_PROJECT_KEY, 1)[1]
        if suffix.isdigit() and int(suffix) < MAX_RECENT_PROJECTS:
            return int(suffix)
    return None


def recent_project_menu(paths):
    # The index key avoids ambiguity between equal filenames or menu shortcuts
    # in paths. Show the full path so projects in different folders are distinct.
    return ([f'{index + 1}. {path}{RECENT_PROJECT_KEY}{index}'
             for index, path in enumerate(paths)] + ['---', CLEAR_RECENT_PROJECTS]
            if paths else ['!No recent projects'])
