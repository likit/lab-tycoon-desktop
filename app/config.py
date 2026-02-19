import logging
import os
import sys
from logging.config import dictConfig

import yaml

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

DATABASE_URI = 'sqlite:///app/labtycoon.db'

# TODO: find a way to dynamically set the secret key when app is first launched.
secret_key = '85015f158b2f8b050705aa6ec9fbd65c99966725eec0965a5ac0bd3564afd210'


def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(base_url, relative_path)

if not os.path.exists(resource_path('config.yaml')):
    config_dict = {
        'num_analyzers': 1,
    }
    with open(os.path.join(resource_path('config.yaml')), 'w') as f:
        yaml.dump(config_dict, f)
else:
    with open(os.path.join(resource_path('config.yaml')), 'r') as f:
        config_dict = yaml.safe_load(f)


def update_config_yaml(**kwargs):
    config_dict.update(kwargs)
    with open(os.path.join(base_url, 'config.yaml'), 'w') as f:
        yaml.dump(config_dict, f)
    print('The app config has been updated.')
