import logging
from logging.config import dictConfig

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

# TODO: find a way to dynamically set the secret key when app is first launched.
secret_key = '85015f158b2f8b050705aa6ec9fbd65c99966725eec0965a5ac0bd3564afd210'
