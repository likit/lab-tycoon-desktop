from flask import request, jsonify
from server.models import User
from flask_restful import Resource

from ..extensions import db


class UserResource(Resource):
    def post(self):
        data = request.get_json()
        print(data)
        user = User(
            firstname=data['0'],
            lastname=data['1'],
            email=data['2'],
            username=data['3'],
        )
        user.password = data['4']
        db.session.add(user)
        db.session.commit()
        return jsonify({'message': 'success'})