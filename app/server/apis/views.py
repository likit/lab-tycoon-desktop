from functools import wraps

from flask import request, jsonify
from http import HTTPStatus
from flask_jwt_extended import jwt_required, get_jwt_identity, current_user, verify_jwt_in_request, get_jwt
from sqlalchemy.exc import IntegrityError

from server.models import User
from flask_restful import Resource

from ..extensions import db


def admin_required():
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            verify_jwt_in_request()
            claims = get_jwt()
            if 'admin' in claims["roles"]:
                return fn(*args, **kwargs)
            else:
                return {'message': 'Admins Only'}, 403
        return decorator
    return wrapper


class UserResource(Resource):
    @jwt_required()
    def get(self):
        user = User.get_user_by_username(current_user)
        if user:
            return user.to_dict()
        else:
            return {'message': 'user not found'}, HTTPStatus.NOT_FOUND

    @jwt_required()
    def put(self):
        data = request.get_json()
        user = User.get_user_by_username(current_user)
        if user:
            user.firstname = data.get('firstname')
            user.lastname = data.get('lastname')
            user.email = data.get('email')
            user.license_id = data.get('license_id')
            db.session.add(user)
            db.session.commit()
            return {'message': 'success'}, HTTPStatus.CREATED
        else:
            return {'message': 'user not found'}, HTTPStatus.NOT_FOUND

    def post(self):
        data = request.get_json()
        user = User(
            firstname=data['firstname'],
            lastname=data['lastname'],
            email=data['email'],
            username=data['username'],
            license_id=data['license_id'],
        )
        user.password = data['password']
        try:
            db.session.add(user)
            db.session.commit()
        except IntegrityError as e:
            return {'message': str(e)}, HTTPStatus.BAD_REQUEST

        return {'message': 'success'}, HTTPStatus.CREATED


class ProtectedResource(Resource):
    @admin_required()
    def get(self):
        return {'message': 'How da hell you get in here?'}