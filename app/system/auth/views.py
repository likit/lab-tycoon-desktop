from datetime import datetime, timezone

# from flask import request, jsonify
# from http import HTTPStatus
# from flask_jwt_extended import (create_access_token, jwt_required, current_user, get_jwt, create_refresh_token)
#
# from system.auth import auth_bp
# from system.extensions import jwt, db
# from system.models import User, TokenBlocklist


# @jwt.token_in_blocklist_loader
# def check_if_token_revoked(jwt_header, jwt_payload: dict) -> bool:
#     jti = jwt_payload["jti"]
#     token = db.session.query(TokenBlocklist.id).filter_by(jti=jti).scalar()
#     return token is not None
#
#
# @jwt.user_lookup_loader
# def user_lookup_callback(_jwt_header, jwt_data):
#     username = jwt_data["sub"]
#     user = User.get_user_by_username(username)
#     return user
#
#
# @auth_bp.route('/sign-in', methods=['POST'])
# def sign_in():
#     data = request.get_json()
#     username = data['username']
#     password = data['password']
#     user = User.get_user_by_username(username)
#     if not user.active:
#         return jsonify({'message': 'Your account has been deactivated. Please contact the admin.'}), HTTPStatus.UNAUTHORIZED
#     if user:
#         if user.check_password(password):
#             return jsonify({'message': 'You have signed in.',
#                             'access_token': create_access_token(identity=username, expires_delta=False,
#                                                                 additional_claims={'roles': user.all_roles})})
#         else:
#             return jsonify({'message': 'Wrong password. You have not been authorized.'}), HTTPStatus.UNAUTHORIZED
#     else:
#         return jsonify({'message': 'The username is not found.'}), HTTPStatus.NOT_FOUND
#
#
# @auth_bp.route('/sign-out', methods=['DELETE'])
# @jwt_required(verify_type=False)
# def sign_out():
#     token = get_jwt()
#     jti = token["jti"]
#     ttype = token["type"]
#     now = datetime.now(timezone.utc)
#     db.session.add(TokenBlocklist(jti=jti, type=ttype, created_at=now))
#     db.session.commit()
#     return jsonify(message=f"{ttype.capitalize()} token successfully revoked")
