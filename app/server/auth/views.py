from flask import request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from server.auth import auth_bp
from server.models import User


@auth_bp.route('/sign-in', methods=['POST'])
def sign_in():
    data = request.get_json()
    username = data['0']
    password = data['1']
    user = User.query.filter_by(username=username).first()
    if user:
        if user.check_password(password):
            return jsonify({'message': 'You have signed in.',
                            'access_token': create_access_token(identity=username)})
        else:
            return jsonify({'message': 'Failed to authenticate the user.'}), 403
    else:
        return jsonify({'message': 'User not found.'}), 404


@auth_bp.route('/sign-out')
@jwt_required()
def sign_out():
    current_user = get_jwt_identity()
    if current_user:
        return {'message': f'You have signed in {current_user}'}
    else:
        return {'message': 'User has not signed in.'}
