from flask import Flask, request

from server.apis.views import UserResource
from server.extensions import db, login_manager, flask_api


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
db.init_app(app)
login_manager.init_app(app)

from server.apis import api_bp

flask_api.init_app(api_bp)

flask_api.add_resource(UserResource, '/register')

app.register_blueprint(api_bp)


@app.route('/kill')
def kill():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()
    return "Shutting down..."

