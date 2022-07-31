from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_restful import Api


db = SQLAlchemy()
login_manager = LoginManager()
flask_api = Api()