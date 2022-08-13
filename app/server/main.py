from http import HTTPStatus

from flask import Flask, request, jsonify

from server.apis.views import (UserResource,
                               AdminUserListResource,
                               AdminUserRoleResource,
                               AdminBioSource,
                               TestListResource, SimulationResource)
from server.extensions import db, flask_api, jwt

from logging.config import dictConfig

dictConfig({
    'version': 1,
    'formatters': {'default': {
        'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    }},
    'handlers': {
        'wsgi': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://flask.logging.wsgi_errors_stream',
            'formatter': 'default'
        },
        'client': {
            'class': 'logging.FileHandler',
            'formatter': 'default',
            'filename': 'run_log.txt'
        }
    },
    'root': {
        'level': 'INFO',
        'handlers': ['wsgi', 'client']
    }
})

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///data/app.db'
app.config['SECRET_KEY'] = 'mumtmahidol'
app.config['JWT_SECRET_KEY'] = 'mumtmahidol'
db.init_app(app)
jwt.init_app(app)

from server.auth import auth_bp

app.register_blueprint(auth_bp)

from server.apis import api_bp

flask_api.init_app(api_bp)

flask_api.add_resource(UserResource, '/users', '/users/<string:username>')
flask_api.add_resource(AdminUserListResource, '/admin/users')
flask_api.add_resource(AdminUserRoleResource, '/admin/users/<string:username>/roles')
flask_api.add_resource(AdminBioSource, '/admin/biosources')
flask_api.add_resource(TestListResource, '/admin/tests')
flask_api.add_resource(SimulationResource, '/simulations')

app.register_blueprint(api_bp)


@app.route('/kill')
def kill():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()
    return "Shutting down..."

