import datetime
import logging
from functools import wraps
import random

from flask import request, jsonify
from werkzeug.security import check_password_hash
from http import HTTPStatus
from flask_jwt_extended import jwt_required, get_jwt_identity, current_user, verify_jwt_in_request, get_jwt
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from server.models import User, UserRole, BioSource, Test, Specimens, TestMethod, Customer, LabOrder, LabOrderItem
from flask_restful import Resource

from ..extensions import db

logger = logging.getLogger('client')


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
    def patch(self, username):
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


class CustomerListResource(Resource):
    @jwt_required()
    def get(self):
        customers = Customer.query.all()
        return {'data': [c.to_dict() for c in customers]}, HTTPStatus.OK


class CustomerResource(Resource):
    @jwt_required()
    def get(self, customer_id):
        customer = Customer.query.get(customer_id)
        cust_data = customer.to_dict()
        cust_data['orders'] = []
        for order in customer.orders:
            order_data = {
                'id': order.id,
                'received_at': order.received_at.isoformat(),
                'items': [],
            }
            for item in order.order_items:
                order_data['items'].append(item.to_dict())
            cust_data['orders'].append(order_data)

        return {'data': cust_data}, HTTPStatus.OK


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
    def patch(self, username=None):
        if username:
            user = User.get_user_by_username(username)
        else:
            user = current_user
        data = request.get_json()
        if user:
            if '-OLD-PWD-' in data:
                if check_password_hash(current_user.hashed_password, data['-OLD-PWD-']):
                    current_user.password = data['-NEW-PWD-']
                    db.session.add(current_user)
                    db.session.commit()
                    return {'message': 'Password has been changed.'}, HTTPStatus.OK
                else:
                    return {'message': 'Wrong Password'}, HTTPStatus.UNAUTHORIZED

            for key in data:
                setattr(user, key, data.get(key))
            db.session.add(user)
            db.session.commit()
            return {'message': 'Data have been updated.'}, HTTPStatus.OK
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

env = simpy.rt.RealtimeEnvironment(factor=0.1, strict=False)
reception = simpy.Resource(env, capacity=1)
instrument = simpy.Resource(env, capacity=1)


def check_item(env, order, waiting_time, check_duration):
    yield env.timeout(waiting_time)

    with reception.request() as req:
        yield req

        logging.info('Checking in...')
        yield env.timeout(check_duration)
        order.received_at = order.order_datetime + datetime.timedelta(minutes=env.now)
        logger.info(f'LAB ORDER ID={order.id} RECEIVED AT {order.received_at}')
        db.session.add(order)
    db.session.commit()


def run_test(env, item, duration):
    start_datetime = datetime.datetime.now()
    with instrument.request() as req:
        yield req
        logger.info(f'LAB ORDER ITEM ID={item.id} STARTED ANALYZING AT {start_datetime + datetime.timedelta(minutes=env.now)}')
        yield env.timeout(duration)
        item.finished_at = start_datetime + datetime.timedelta(minutes=env.now)
        logger.info(f'LAB ORDER ITEM ID={item.id} FINISHED AT {item.finished_at}')
        db.session.add(item)
        db.session.commit()


class SimulationResource(Resource):
    @jwt_required()
    def get(self):
        customer = Customer.query.order_by(func.random()).first()
        tests = [t for t in Test.query.all()]
        order = LabOrder(customer=customer, order_datetime=datetime.datetime.now())
        n = 5 if len(tests) > 5 else len(tests)
        for test in random.choices(tests, k=n):
            order_item = LabOrderItem(test=test)
            order.order_items.append(order_item)
        env.process(check_item(env, order, 2, 1))
        env.run()
        db.session.commit()
        return {'message': 'done'}


class AnalyzerResource(Resource):
    @jwt_required()
    def get(self):
        items = LabOrderItem.query.filter(LabOrderItem.finished_at==None)\
            .filter(LabOrderItem.cancelled_at==None).all()
        if not items:
            return {'message': 'Nothing to analyze.'}, HTTPStatus.OK
        for item in items:
            env.process(run_test(env, item, random.randint(10, 40)))
        env.run()
        return {'message': 'Analyses finished.'}, HTTPStatus.OK


class OrderListResource(Resource):
    @jwt_required()
    def get(self):
        orders = []
        for order in LabOrder.query.order_by(LabOrder.order_datetime.desc()):
            orders.append({
                'id': order.id,
                'order_datetime': order.order_datetime.isoformat(),
                'received_datetime': order.received_at.isoformat(),
                'rejected_datetime': order.rejected_at.isoformat() if order.rejected_at else None,
                'rejected_by': order.rejector.lastname if order.rejector else None,
                'cancelled_datetime': order.cancelled_at.isoformat() if order.cancelled_at else None,
                'cancelled_by': order.canceller.lastname if order.canceller else None,
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
                    'rejected_at': order.rejected_at.isoformat() if order.rejected_at else None,
                    'rejected_by': order.rejector.lastname if order.rejector else None,
                    'firstname': order.customer.firstname,
                    'lastname': order.customer.lastname,
                    'hn': order.customer.hn,
                    'items': [t.to_dict() for t in order.order_items]
                }
            }

    @jwt_required()
    def patch(self, lab_order_id):
        order = LabOrder.query.get(lab_order_id)
        if not order:
            return {'message': 'Lab order not found.'}, HTTPStatus.NOT_FOUND
        data = request.get_json()
        for key in data:
            if key in ['cancelled_at', 'rejected_at']:
                setattr(order, key, datetime.datetime.fromisoformat(data[key]))
                if key == 'cancelled_at':
                    message = 'The order has been cancelled.'
                    order.canceller = current_user
                    for item in order.order_items:
                        item.cancelled_at = datetime.datetime.fromisoformat(data['cancelled_at'])
                        db.session.add(item)
                        logger.info(f'{current_user.username} REJECTED LAB ORDER ID={order.id}:'
                                    ' CANCELLED LAB ORDER ITEM ID={item.id}')
                    logger.info(f'{current_user.username} CANCELLED LAB ORDER ID={order.id}')
                elif key == 'rejected_at':
                    message = 'The order has been rejected.'
                    order.rejector = current_user
                    # Cancel all items in the order also.
                    for item in order.order_items:
                        item.cancelled_at = datetime.datetime.fromisoformat(data['rejected_at'])
                        db.session.add(item)
                        logger.info(f'{current_user.username} REJECTED LAB ORDER ID={order.id}:'
                                    ' CANCELLED LAB ORDER ITEM ID={item.id}')
                    logger.info(f'{current_user.username} REJECTED LAB ORDER ID={order.id}')
            else:
                setattr(order, key, data[key])
        db.session.add(order)
        db.session.commit()
        return {'message': 'Updated successfully'}, HTTPStatus.OK


class OrderItemListResource(Resource):
    @jwt_required()
    def get(self):
        unfinished = request.args.get('unfinished')
        if unfinished == 'true':
            items = LabOrderItem.query.filter(LabOrderItem.finished_at==None)\
                .filter(LabOrderItem.cancelled_at==None).all()
        else:
            items = LabOrderItem.query.all()
        return {'data': [t.to_dict() for t in items]}


class OrderItemResource(Resource):
    @jwt_required()
    def get(self, lab_order_item_id):
        item = LabOrderItem.query.get(lab_order_item_id)
        if not item:
            return {'message': 'Lab order item not found.'}, HTTPStatus.NOT_FOUND
        else:
            return {'data': item.to_dict()}

    @jwt_required()
    def patch(self, lab_order_item_id):
        item = LabOrderItem.query.get(lab_order_item_id)
        if not item:
            return {'message': 'Lab order item not found.'}, HTTPStatus.NOT_FOUND
        else:
            data = request.get_json()
            for key in data:
                if key in ['cancelled_at', 'reported_at', 'approved_at', 'finished_at']:
                    setattr(item, key, datetime.datetime.fromisoformat(data[key]))
                    if key == 'cancelled_at':
                        message = 'The item has been cancelled.'
                        item.canceller = current_user
                        logger.info(f'{current_user.username} CANCELLED LAB ORDER ITEM ID={item.id}')
                    elif key == 'reported_at':
                        message = 'The report has been made.'
                        if current_user.has_role('reporter'):
                            item.reporter = current_user
                            logger.info(f'{current_user.username} REPORTED LAB ORDER ITEM ID={item.id}')
                        else:
                            return {'message': 'Reporter role is required.'}, HTTPStatus.UNAUTHORIZED
                    elif key == 'approved_at':
                        message = 'The item has been approved.'
                        if current_user.has_role('approver'):
                            item.approver = current_user
                            logger.info(f'{current_user.username} APPROVED LAB ORDER ITEM ID={item.id}')
                        else:
                            return {'message': 'Approver role is required.'}, HTTPStatus.UNAUTHORIZED
                else:
                    setattr(item, key, data[key])
            item.updater = current_user
            item.updated_at = datetime.datetime.now()
            db.session.add(item)
            db.session.commit()
            return {'message': message}, HTTPStatus.OK


class OrderItemVersionListResource(Resource):
    @jwt_required()
    def get(self, lab_order_item_id):
        item = LabOrderItem.query.get(lab_order_item_id)
        if not item:
            return {'message': 'Lab order item not found.'}, HTTPStatus.NOT_FOUND
        else:
            versions = []
            for ver in item.versions:
                if ver._value and ver.test.scale == 'Quantitative':
                    value = float(ver._value)
                else:
                    value = ver._value
                versions.append({
                    'value': value,
                    'comment': ver.comment,
                    'reported_at': ver.reported_at.isoformat() if ver.reported_at else None,
                    'approved_at': ver.reported_at.isoformat() if ver.approved_at else None,
                    'updated_at': ver.reported_at.isoformat() if ver.updated_at else None,
                    'approver_name': ver.approver.lastname if ver.approver else '',
                    'reporter_name': ver.reporter.lastname if ver.reporter else '',
                    'updater_name': ver.updater.lastname if ver.updater else '',
                })
            return {'data': versions}
