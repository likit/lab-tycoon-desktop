from flask import Flask, request

from server.apis.views import UserResource
from server.extensions import db, flask_api, jwt

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
app.config['SECRET_KEY'] = 'mumtmahidol'
app.config['JWT_SECRET_KEY'] = 'mumtmahidol'
db.init_app(app)
jwt.init_app(app)

from server.auth import auth_bp

app.register_blueprint(auth_bp)

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

