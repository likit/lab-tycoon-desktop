from flask import request, jsonify
from http import HTTPStatus
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy.exc import IntegrityError

from server.models import User
from flask_restful import Resource

from ..extensions import db


class UserResource(Resource):
    @jwt_required()
    def get(self):
        current_user = get_jwt_identity()
        user = User.get_user_by_username(current_user)
        if user:
            return user.to_dict()
        else:
            return {'message': 'user not found'}, HTTPStatus.NOT_FOUND

    @jwt_required()
    def put(self):
        current_user = get_jwt_identity()
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