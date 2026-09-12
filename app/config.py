import logging
import os
from pathlib import Path
import sys
from logging.config import dictConfig

import yaml

from app.database import DatabaseConfig, DatabaseManager

base_url = os.path.dirname(os.path.abspath(__file__))

current_user = None

dictConfig({
    'version': 1,
    'formatters': {'default': {
        'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    }},
    'handlers': {
        'file': {
            'class': 'logging.FileHandler',
            'formatter': 'default',
            'filename': 'run_log.txt'
        }
    },
    'loggers': {
        'client': {
            'level': 'INFO',
            'handlers': ['file']
        },
    },
})

logger = logging.getLogger('client')


def get_app_data_dir(app_name='LabTycoon'):
    """Return a writable application data directory for the desktop app."""

    home = Path.home()

    if sys.platform == 'darwin':
        preferred = home / 'Library' / 'Application Support' / app_name
    elif sys.platform.startswith('win'):
        appdata = os.environ.get('APPDATA')
        preferred = Path(appdata) / app_name if appdata else home / 'AppData' / 'Roaming' / app_name
    else:
        xdg_data_home = os.environ.get('XDG_DATA_HOME')
        preferred = Path(xdg_data_home) / app_name if xdg_data_home else home / '.local' / 'share' / app_name

    try:
        preferred.mkdir(parents=True, exist_ok=True)
    except OSError:
        fallback = base_url / Path('instance')
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    return preferred


APP_DATA_DIR = get_app_data_dir()
DEFAULT_DATABASE_PATH = APP_DATA_DIR / 'labtycoon.db'

# TODO: find a way to dynamically set the secret key when app is first launched.
secret_key = '85015f158b2f8b050705aa6ec9fbd65c99966725eec0965a5ac0bd3564afd210'



if not os.path.exists('config.yaml'):
    config_dict = {
        'num_analyzers': 1,
        'active_workflow_id': None,
        'last_project_path': None,
        'database': DatabaseConfig.default_sqlite(DEFAULT_DATABASE_PATH).to_dict(),
    }
    with open('config.yaml', 'w') as f:
        yaml.dump(config_dict, f)
else:
    with open('config.yaml', 'r') as f:
        config_dict = yaml.safe_load(f)
    if config_dict is None:
        config_dict = {
            'num_analyzers': 1,
            'active_workflow_id': None,
            'last_project_path': None,
            'database': DatabaseConfig.default_sqlite(DEFAULT_DATABASE_PATH).to_dict(),
        }

DATABASE_CONFIG = DatabaseConfig.from_dict(
    config_dict.get('database'),
    default_database_path=DEFAULT_DATABASE_PATH,
)
DATABASE_MANAGER = DatabaseManager(DATABASE_CONFIG)
DATABASE_PATH = Path(DATABASE_CONFIG.database_path)
# Retained for compatibility with code outside the central models module.
DATABASE_URI = str(DATABASE_MANAGER.sqlalchemy_url())


def update_config_yaml(**kwargs):
    config_dict.update(kwargs)
    with open('config.yaml', 'w') as f:
        yaml.dump(config_dict, f)
    print('The app config has been updated.')
