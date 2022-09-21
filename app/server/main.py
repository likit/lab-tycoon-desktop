from flask import Flask, request

from server.apis.views import (UserResource,
                               AdminUserListResource,
                               AdminUserRoleResource,
                               AdminBioSource,
                               TestListResource, SimulationResource, OrderListResource, OrderResource,
                               OrderItemResource, OrderItemListResource, AnalyzerResource)
from server.extensions import db, flask_api, jwt

from logging.config import dictConfig

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
flask_api.add_resource(OrderListResource, '/orders')
flask_api.add_resource(OrderResource, '/orders/<int:lab_order_id>')
flask_api.add_resource(OrderItemResource, '/order-items/<int:lab_order_item_id>')
flask_api.add_resource(OrderItemListResource, '/order-items')
flask_api.add_resource(AnalyzerResource, '/analyses')

app.register_blueprint(api_bp)


@app.route('/kill')
def kill():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()
    return "Shutting down..."

