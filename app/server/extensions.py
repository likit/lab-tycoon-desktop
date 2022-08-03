from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_restful import Api


db = SQLAlchemy()
flask_api = Api()
jwt = JWTManager()