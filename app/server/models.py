from flask_jwt_extended import get_current_user
from sqlalchemy import func

from server.extensions import db
from werkzeug.security import generate_password_hash, check_password_hash


class TokenBlocklist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(36), nullable=False, index=True)
    type = db.Column(db.String(16), nullable=False)
    user_id = db.Column(
        db.ForeignKey('users.id'),
        default=lambda: get_current_user().id,
        nullable=False,
    )
    created_at = db.Column(
        db.DateTime,
        server_default=func.now(),
        nullable=False,
    )


class UserRole(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    role_need = db.Column('role_need', db.String(), nullable=True)
    action_need = db.Column('action_need', db.String())
    resource_id = db.Column('resource_id', db.Integer())

    def to_tuple(self):
        return self.role_need, self.action_need, self.resource_id

    def __str__(self):
        return u'Role {}: can {} -> resource ID {}'.format(self.role_need, self.action_need, self.resource_id)


user_roles = db.Table('user_roles',
                      db.Column('user_id', db.Integer(), db.ForeignKey('users.id')),
                      db.Column('role_id', db.Integer(), db.ForeignKey('roles.id')))


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    username = db.Column(db.String(255), nullable=False, unique=True)
    email = db.Column(db.String(255), nullable=True, unique=True)
    hashed_password = db.Column(db.String(255), nullable=False)
    firstname = db.Column('firstname', db.String(255), nullable=False)
    lastname = db.Column('lastname', db.String(255), nullable=False)
    license_id = db.Column('license_id', db.String(255), nullable=True)
    position = db.Column('position', db.String(255), nullable=True)
    roles = db.relationship(UserRole, secondary=user_roles, backref=db.backref('users', lazy='dynamic'))

    @classmethod
    def get_user_by_username(cls, username):
        return cls.query.filter_by(username=username).first()

    @property
    def password(self):
        raise ValueError('Password is not accessible.')

    @password.setter
    def password(self, new_password):
        self.hashed_password = generate_password_hash(new_password)

    def check_password(self, password):
        return check_password_hash(self.hashed_password, password)

    def to_dict(self):
        return {
            'username': self.username,
            'email': self.email,
            'firstname': self.firstname,
            'lastname': self.lastname,
            'license_id': self.license_id,
            'position': self.position,
            'roles': self.all_roles
        }

    @property
    def all_roles(self):
        return ','.join([r.role_need for r in self.roles])


class BioSource(db.Model):
    __tablename__ = 'biosources'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    source = db.Column('source', db.String(255), nullable=False)


class Specimens(db.Model):
    __tablename__ = 'specimens'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    label = db.Column('label', db.String(255), nullable=False)
    source_id = db.Column('source_id', db.ForeignKey('biosources.id'))
    source = db.relationship(BioSource, backref=db.backref('specimens', lazy='dynamic', cascade='all, delete-orphan'))
    desc = db.Column('desc', db.Text(), nullable=True)


class TestMethod(db.Model):
    __tablename__ = 'test_methods'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    method = db.Column('method', db.String(), nullable=False)


class Test(db.Model):
    __tablename__ = 'tests'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    code = db.Column('code', db.String(), nullable=False, unique=True, index=True)
    tmlt_code = db.Column('tmlt_code', db.String(), unique=True, index=True)
    tmlt_name = db.Column('tmlt_name', db.String())
    loinc_no = db.Column('loinc_no', db.String(), unique=True)
    component = db.Column('component', db.String())
    label = db.Column('label', db.String(), nullable=False)
    scale = db.Column('scale', db.String(), nullable=False)
    specimens_id = db.Column(db.ForeignKey('specimens.id'))
    method_id = db.Column(db.ForeignKey('test_methods.id'))
    price = db.Column('price', db.Numeric(), default=0.0, nullable=False)
    desc = db.Column('desc', db.Text())
    unit = db.Column('unit', db.String(), nullable=False)
    order_type = db.Column('order_type', db.String())
    cgd_code = db.Column('cgd_code', db.String(), unique=True)
    cgd_name = db.Column('cgd_name', db.String())
    cgd_price = db.Column('cgd_price', db.Numeric())
    panel = db.Column('panel', db.String())
    ref_min = db.Column('ref_min', db.Numeric())
    ref_max = db.Column('ref_max', db.Numeric())
    value_choices = db.Column('value_choices', db.String())
    method = db.relationship(TestMethod, backref=db.backref('tests'))
    specimens = db.relationship(Specimens, backref=db.backref('tests'))
    active = db.Column('active', db.Boolean(), default=True)

    def __init__(self, code, tmlt_code, tmlt_name, loinc_no, specimens, method,
                 component, label, scale, price, desc, unit, order_type,
                 cgd_code, cgd_name, cgd_price, panel, ref_min, ref_max, value_choices):
        self.code = code
        self.tmlt_code = tmlt_code
        self.tmlt_name = tmlt_name
        self.loinc_no = loinc_no
        self.component = component
        self.label = label
        self.scale = scale
        self.price = float(price) if price else None
        self.desc = desc
        self.unit = unit
        self.order_type = order_type
        self.cgd_code = cgd_code
        self.cgd_name = cgd_name
        self.cgd_price = float(cgd_price) if cgd_price else None
        self.panel = panel
        self.ref_min = float(ref_min) if ref_min else None
        self.ref_max = float(ref_max) if ref_max else None
        self.value_choices = value_choices

    def to_dict(self):
        return {
            'code': self.code,
            'tmlt_code': self.tmlt_code,
            'tmlt_name': self.tmlt_name,
            'loinc_no': self.loinc_no,
            'component': self.component,
            'label': self.label,
            'scale': self.scale,
            'price': float(self.price) if self.price else None,
            'desc': self.desc,
            'specimens': self.specimens.label,
            'method': self.method.method,
            'unit': self.unit,
            'order_type': self.order_type,
            'cgd_code': self.cgd_code,
            'cgd_name': self.cgd_name,
            'cgd_price': float(self.cgd_price) if self.cgd_price else None,
            'panel': self.panel,
            'ref_min': float(self.ref_min) if self.ref_min else None,
            'ref_max': float(self.ref_max) if self.ref_max else None,
            'value_choices': self.value_choices,
            'active': self.active
        }


class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    hn = db.Column('hn', db.String(), unique=True)
    firstname = db.Column('firstname', db.String())
    lastname = db.Column('lastname', db.String())
    gender = db.Column('gender', db.String())
    dob = db.Column('dob', db.Date())
    address = db.Column('address', db.Text())


class LabOrder(db.Model):
    __tablename__ = 'lab_orders'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    customer_id = db.Column('customer_id', db.ForeignKey('customers.id'))
    order_datetime = db.Column('order_datetime', db.DateTime())
    cancelled_at = db.Column('cancelled_at', db.DateTime())
    received_at = db.Column('received_at', db.DateTime())
    customer = db.relationship(Customer, backref=db.backref('orders'))
    released_at = db.Column('released_at', db.DateTime())


class LabOrderItem(db.Model):
    __tablename__ = 'lab_order_items'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    order_id = db.Column('order_id', db.ForeignKey('lab_orders.id'))
    test_id = db.Column('test_id', db.ForeignKey('tests.id'))
    test = db.relationship(Test, backref=db.backref('order_items', lazy='dynamic', cascade='all, delete-orphan'))
    order = db.relationship(LabOrder, backref=db.backref('order_items', lazy='dynamic', cascade='all, delete-orphan'))
    comment = db.Column('comment', db.Text())
    cancelled_at = db.Column('cancelled_at', db.DateTime())
    finished_at = db.Column('finished_at', db.DateTime())
    reported_at = db.Column('reported_at', db.DateTime())
    approved_at = db.Column('approved_at', db.DateTime())
    approver_id = db.Column('approver_id', db.ForeignKey('users.id'))
    reporter_id = db.Column('reporter_id', db.ForeignKey('users.id'))
    approver = db.relationship(User, foreign_keys=[approver_id])
    reporter = db.relationship(User, foreign_keys=[reporter_id])
    _value = db.Column('value', db.String(), nullable=True)

    @property
    def value(self):
        if self.order_item.test.value_type == 'Quantitative':
            return float(self._value)
        else:
            return self._value

    @property
    def value_string(self):
        return f'{self._value} {self.order_item.test.unit}'


class LabRejectRecord(db.Model):
    __tablename__ = 'lab_reject_records'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    order_item_id = db.Column('order_item_id', db.ForeignKey('lab_order_items.id'))
    order_item = db.relationship(LabOrderItem, backref=db.backref('reject_records'))
    rejected_at = db.Column('rejected_at', db.DateTime())
    reason = db.Column('reason', db.Text())
