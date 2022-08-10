from functools import wraps

from flask import request, jsonify
from http import HTTPStatus
from flask_jwt_extended import jwt_required, get_jwt_identity, current_user, verify_jwt_in_request, get_jwt
from sqlalchemy.exc import IntegrityError

from server.models import User, UserRole, BioSource, Test, Specimens, TestMethod
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


class AdminUserListResource(Resource):
    @admin_required()
    def get(self):
        data = []
        for user in User.query.all():
            data.append(user.to_dict())
        return {'data': data}


class AdminUserRoleResource(Resource):
    @admin_required()
    def put(self, username):
        roles = request.get_json()
        user = User.get_user_by_username(username)
        user.roles = []
        for role in UserRole.query.all():
            if roles[role.role_need] is True:
                user.roles.append(role)
        db.session.add(user)
        db.session.commit()
        return {'message': 'Roles have been updated.'}, HTTPStatus.CREATED


class AdminBioSource(Resource):
    @admin_required()
    def get(self):
        data = []
        for src in BioSource.query.all():
            data.append({
                'source': src.source
            })
        return {'data': data}


class UserResource(Resource):
    @jwt_required()
    def get(self, username=None):
        if username is None and current_user:
            return current_user.to_dict()
        else:
            user = User.get_user_by_username(username)
            if user:
                return user.to_dict()
        return {'message': 'user not found'}, HTTPStatus.NOT_FOUND

    @jwt_required()
    def put(self):
        data = request.get_json()
        if current_user:
            current_user.firstname = data.get('firstname')
            current_user.lastname = data.get('lastname')
            current_user.email = data.get('email')
            current_user.position = data.get('position')
            current_user.license_id = data.get('license_id')
            db.session.add(current_user)
            db.session.commit()
            return {'message': 'Data have been updated.'}, HTTPStatus.CREATED
        else:
            return {'message': 'user not found'}, HTTPStatus.NOT_FOUND

    def post(self):
        data = request.get_json()
        user = User(
            firstname=data['firstname'],
            lastname=data['lastname'],
            email=data['email'],
            position=data['position'],
            username=data['username'],
            license_id=data['license_id'],
        )
        user.password = data['password']
        try:
            db.session.add(user)
            db.session.commit()
        except IntegrityError as e:
            return {'message': str(e)}, HTTPStatus.BAD_REQUEST

        return {'message': 'New user have been registered.'}, HTTPStatus.CREATED


class TestResource(Resource):
    @admin_required()
    def post(self):
        data = request.get_json()
        test = Test(**data)
        specimens_obj = Specimens.query.filter_by(label=data['specimens']).first()
        if specimens_obj:
            test.specimens = specimens_obj
        else:
            new_specimens_obj = Specimens(label=data['specimens'])
            db.session.add(new_specimens_obj)
            test.specimens = new_specimens_obj

        method_obj = TestMethod.query.filter_by(method=data['method']).first()
        if method_obj:
            test.method = method_obj
        else:
            new_method_obj = TestMethod(method=data['method'])
            db.session.add(new_method_obj)
            test.method = new_method_obj
        db.session.add(test)
        db.session.commit()
        return {'message': 'New test added.'}, HTTPStatus.CREATED
