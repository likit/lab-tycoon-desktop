import datetime
import logging
from functools import wraps
import random

from flask import request, jsonify
from http import HTTPStatus
from flask_jwt_extended import jwt_required, get_jwt_identity, current_user, verify_jwt_in_request, get_jwt
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from server.models import User, UserRole, BioSource, Test, Specimens, TestMethod, Customer, LabOrder, LabOrderItem
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
            db.session.rollback()
            return {'message': str(e)}, HTTPStatus.BAD_REQUEST

        return {'message': 'New user have been registered.'}, HTTPStatus.CREATED


class TestListResource(Resource):
    @jwt_required()
    def get(self):
        data = [d.to_dict() for d in Test.query.filter_by(active=True)]
        return {'data': data, 'message': 'Done'}, HTTPStatus.OK

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
        try:
            db.session.add(test)
            db.session.commit()
        except IntegrityError as e:
            db.session.rollback()
            return {'message': str(e)}, HTTPStatus.BAD_REQUEST
        return {'message': 'New test added.'}, HTTPStatus.CREATED



import simpy

env = simpy.rt.RealtimeEnvironment(factor=1.0, strict=False)
reception = simpy.Resource(env, capacity=1)
instrument = simpy.Resource(env, capacity=1)


def run_test(env, order_item, run_duration):
    reporters = [u for u in User.query.all() if 'reporter' in u.all_roles]
    approvers = [u for u in User.query.all() if 'approver' in u.all_roles]

    reporter = simpy.Resource(env, capacity=len(reporters))
    approver = simpy.Resource(env, capacity=len(approvers))

    with instrument.request() as req:
        yield req

        logging.info(f'Analyzing {order_item}...')
        yield env.timeout(run_duration)
        order_item.finished_at = order_item.order.order_datetime + datetime.timedelta(minutes=env.now)

    with reporter.request() as req:
        yield req
        logging.info(f'Reporting {order_item}...')
        order_item.reporter = random.choices(reporters)[0]
        order_item.reported_at = order_item.order.order_datetime + datetime.timedelta(minutes=env.now)

    with approver.request() as req:
        yield req
        logging.info(f'Approving {order_item}...')
        order_item.approved_at = order_item.order.order_datetime + datetime.timedelta(minutes=env.now)
        order_item.approver = random.choices(approvers)[0]
        db.session.add(order_item)


def check_item(env, order, waiting_time, check_duration):
    yield env.timeout(waiting_time)
    logging.info(f'{order} is arrived at {env.now}')

    with reception.request() as req:
        yield req

        logging.info('Checking in...')
        yield env.timeout(check_duration)
        order.received_at = order.order_datetime + datetime.timedelta(minutes=env.now)
        db.session.add(order)

    for test in order.order_items.all():
        yield env.process(run_test(env, test, 2))


class SimulationResource(Resource):
    @jwt_required()
    def get(self):
        customer = Customer.query.order_by(func.random()).first()
        tests = [t for t in Test.query.all()]
        logging.info(tests)
        order = LabOrder(customer=customer, order_datetime=datetime.datetime.now())
        n = 5 if len(tests) > 5 else len(tests)
        for test in random.choices(tests, k=n):
            order_item = LabOrderItem(test=test)
            order.order_items.append(order_item)
        env.process(check_item(env, order, 2, 1))
        env.run()
        db.session.commit()
        return {'message': 'done'}


class OrderListResource(Resource):
    @jwt_required()
    def get(self):
        orders = []
        for order in LabOrder.query.order_by(LabOrder.order_datetime.desc()):
            orders.append({
                'id': order.id,
                'order_datetime': order.order_datetime.isoformat(),
                'received_datetime': order.received_at.isoformat(),
                'firstname': order.customer.firstname,
                'lastname': order.customer.lastname,
                'hn': order.customer.hn,
                'items': order.order_items.count()
            })
        return {'data': orders}


class OrderResource(Resource):
    @jwt_required()
    def get(self, lab_order_id):
        order = LabOrder.query.get(lab_order_id)
        if not order:
            return {'message': 'Lab order not found.'}, HTTPStatus.NOT_FOUND
        else:
            return {
                'data': {
                    'id': order.id,
                    'customer_id': order.customer_id,
                    'order_datetime': order.order_datetime.isoformat() if order.order_datetime else None,
                    'released_at': order.released_at.isoformat() if order.released_at else None,
                    'cancelled_at': order.cancelled_at.isoformat() if order.cancelled_at else None,
                    'received_at': order.received_at.isoformat() if order.received_at else None,
                    'firstname': order.customer.firstname,
                    'lastname': order.customer.lastname,
                    'hn': order.customer.hn,
                    'items': [t.to_dict() for t in order.order_items]
                }
            }
