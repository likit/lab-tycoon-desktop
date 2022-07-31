from flask import request, jsonify
from server.models import User

from . import api_bp
from ..extensions import db


@api_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    print(data)
    user = User(
        firstname=data['0'],
        lastname=data['1'],
        email=data['2'],
        username=data['3'],
        hashed_password=data['4']
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({'message': 'success'})