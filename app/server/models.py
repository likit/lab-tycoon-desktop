import os
import random

import bcrypt
from faker import Faker
from typing import List

from sqlalchemy_continuum import make_versioned
from sqlalchemy import create_engine
from sqlalchemy import (
    ForeignKey, String, Integer,
    Table, Column, Boolean,
    Text, Numeric, Date,
    DateTime
)
from sqlalchemy.orm import DeclarativeBase, configure_mappers, Session, dynamic_loader
from sqlalchemy.orm import Mapped, mapped_column, relationship

from datetime import datetime, date

make_versioned()

engine = create_engine('sqlite:///:memory:')


class Base(DeclarativeBase):
    pass


class UserRole(Base):
    __tablename__ = 'roles'
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)
    role_need: Mapped[str] = mapped_column('role_need', String(), nullable=True)
    action_need: Mapped[str] = mapped_column('action_need', String(), nullable=True)
    resource_id: Mapped[int] = mapped_column('resource_id', Integer(), nullable=True)

    def to_tuple(self):
        return self.role_need, self.action_need, self.resource_id

    def __str__(self):
        return u'Role {}: can {} -> resource ID {}'.format(self.role_need, self.action_need, self.resource_id)


user_roles = Table('user_roles', Base.metadata,
                   Column('user_id', Integer(), ForeignKey('users.id')),
                   Column('role_id', Integer(), ForeignKey('roles.id')))


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(Integer(), autoincrement=True, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=True, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    firstname: Mapped[str] = mapped_column('firstname', String(255), nullable=False)
    lastname: Mapped[str] = mapped_column('lastname', String(255), nullable=False)
    license_id: Mapped[int] = mapped_column('license_id', String(255), nullable=True)
    position: Mapped[int] = mapped_column('position', String(255), nullable=True)
    roles: Mapped[List["UserRole"]] = relationship(secondary=user_roles)
    active: Mapped[bool] = mapped_column('active', Boolean(), default=True)

    @classmethod
    def get_user_by_username(cls, username):
        return cls.query.filter_by(username=username).first()

    @property
    def password(self):
        raise ValueError('Password is not accessible.')

    @password.setter
    def password(self, new_password):
        self.hashed_password = bcrypt.hashpw(new_password.encode('UTF-8'), bcrypt.gensalt())

    def check_password(self, password):
        return bcrypt.checkpw(password, self.hashed_password)

    def to_dict(self):
        return {
            'username': self.username,
            'email': self.email,
            'firstname': self.firstname,
            'lastname': self.lastname,
            'license_id': self.license_id,
            'position': self.position,
            'roles': self.all_roles,
            'active': self.active,
        }

    @property
    def all_roles(self):
        return ','.join([r.role_need for r in self.roles])

    def has_role(self, role_need):
        return role_need in [r.role_need for r in self.roles]


class BioSource(Base):
    __tablename__ = 'biosources'
    id: Mapped[int] = mapped_column(Integer(), autoincrement=True, primary_key=True)
    source: Mapped[str] = mapped_column('source', String(255), nullable=False)
    specimens: Mapped[List["Specimens"]] = relationship(back_populates="source", cascade='all, delete-orphan')


class Specimens(Base):
    __tablename__ = 'specimens'
    id: Mapped[int] = mapped_column(Integer(), autoincrement=True, primary_key=True)
    label: Mapped[str] = mapped_column('label', String(255), nullable=False)
    source_id: Mapped[int] = mapped_column('source_id', ForeignKey('biosources.id'))
    source: Mapped["BioSource"] = relationship(back_populates="specimens")
    desc: Mapped[str] = mapped_column('desc', Text(), nullable=True)
    tests: Mapped[List["Test"]] = relationship(back_populates="specimens")


class TestMethod(Base):
    __tablename__ = 'test_methods'
    id: Mapped[int] = mapped_column(Integer(), autoincrement=True, primary_key=True)
    method: Mapped[str] = mapped_column('method', String(), nullable=False)
    tests: Mapped[List["Test"]] = relationship(back_populates="method")


class Test(Base):
    __tablename__ = 'tests'
    id: Mapped[int] = mapped_column(Integer(), autoincrement=True, primary_key=True)
    code: Mapped[int] = mapped_column('code', String(), nullable=False, unique=True, index=True)
    tmlt_code: Mapped[int] = mapped_column('tmlt_code', String(), unique=True, index=True)
    tmlt_name: Mapped[int] = mapped_column('tmlt_name', String())
    loinc_no: Mapped[int] = mapped_column('loinc_no', String(), unique=True)
    component: Mapped[int] = mapped_column('component', String())
    label: Mapped[int] = mapped_column('label', String(), nullable=False)
    scale: Mapped[int] = mapped_column('scale', String(), nullable=False)
    specimens_id: Mapped[int] = mapped_column(ForeignKey('specimens.id'))
    method_id: Mapped[int] = mapped_column(ForeignKey('test_methods.id'))
    price: Mapped[int] = mapped_column('price', Numeric(), default=0.0, nullable=False)
    desc: Mapped[int] = mapped_column('desc', Text())
    unit: Mapped[int] = mapped_column('unit', String(), nullable=False)
    order_type: Mapped[int] = mapped_column('order_type', String())
    cgd_code: Mapped[int] = mapped_column('cgd_code', String(), unique=True)
    cgd_name: Mapped[int] = mapped_column('cgd_name', String())
    cgd_price: Mapped[int] = mapped_column('cgd_price', Numeric())
    panel: Mapped[int] = mapped_column('panel', String())
    ref_min: Mapped[int] = mapped_column('ref_min', Numeric())
    ref_max: Mapped[int] = mapped_column('ref_max', Numeric())
    value_choices: Mapped[int] = mapped_column('value_choices', String())
    method: Mapped["TestMethod"] = relationship(back_populates="tests")
    specimens: Mapped["Specimens"] = relationship(back_populates="tests")
    active: Mapped[bool] = mapped_column('active', Boolean(), default=True)
    order_items: Mapped[List["LabOrderItem"]] = relationship(back_populates="test", lazy="dynamic")

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


class Customer(Base):
    __tablename__ = 'customers'
    id: Mapped[int] = mapped_column(Integer(), autoincrement=True, primary_key=True)
    hn: Mapped[int] = mapped_column('hn', String(), unique=True)
    firstname: Mapped[int] = mapped_column('firstname', String())
    lastname: Mapped[int] = mapped_column('lastname', String())
    gender: Mapped[int] = mapped_column('gender', String())
    dob: Mapped[date] = mapped_column('dob', Date())
    address: Mapped[int] = mapped_column('address', Text())
    orders: Mapped[List["LabOrder"]] = relationship(back_populates="customer", cascade="all, delete-orphan")

    @property
    def fullname(self):
        return f'{self.firstname} {self.lastname}'

    def to_dict(self):
        return {
            'id': self.id,
            'hn': self.hn,
            'firstname': self.firstname,
            'lastname': self.lastname,
            'fullname': self.fullname,
            'dob': self.dob.isoformat(),
            'address': self.address
        }


class LabOrder(Base):
    __tablename__ = 'lab_orders'
    id: Mapped[int] = mapped_column(Integer(), autoincrement=True, primary_key=True)
    customer_id: Mapped[int] = mapped_column('customer_id', ForeignKey('customers.id'))
    order_datetime: Mapped[datetime] = mapped_column('order_datetime', DateTime())
    cancelled_at: Mapped[datetime] = mapped_column('cancelled_at', DateTime())
    canceller_id: Mapped[int] = mapped_column('canceller_id', ForeignKey('users.id'))
    canceller: Mapped["User"] = relationship(foreign_keys=[canceller_id])
    received_at: Mapped[datetime] = mapped_column('received_at', DateTime())
    customer: Mapped["Customer"] = relationship(back_populates="orders")
    released_at: Mapped[datetime] = mapped_column('released_at', DateTime())
    rejected_at: Mapped[datetime] = mapped_column('rejected_at', DateTime())
    reason: Mapped[str] = mapped_column('reason', String())
    comment: Mapped[str] = mapped_column('comment', Text())
    rejector_id: Mapped[int] = mapped_column('rejector_id', ForeignKey('users.id'))
    rejector: Mapped["User"] = relationship(foreign_keys=[rejector_id])
    order_items: Mapped[List["LabOrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class LabOrderItem(Base):
    __versioned__ = {}
    __tablename__ = 'lab_order_items'
    id: Mapped[int] = mapped_column(Integer(), autoincrement=True, primary_key=True)
    order_id: Mapped[int] = mapped_column('order_id', ForeignKey('lab_orders.id'))
    test_id: Mapped[int] = mapped_column('test_id', ForeignKey('tests.id'))
    test: Mapped["Test"] = relationship(back_populates='order_items')
    order: Mapped["LabOrder"] = relationship(back_populates='order_items')
    comment: Mapped[str] = mapped_column('comment', Text())
    cancelled_at: Mapped[datetime] = mapped_column('cancelled_at', DateTime())
    finished_at: Mapped[datetime] = mapped_column('finished_at', DateTime())
    updated_at: Mapped[datetime] = mapped_column('updated_at', DateTime())
    reported_at: Mapped[datetime] = mapped_column('reported_at', DateTime())
    approved_at: Mapped[datetime] = mapped_column('approved_at', DateTime())
    approver_id: Mapped[int] = mapped_column('approver_id', ForeignKey('users.id'))
    updater_id: Mapped[int] = mapped_column('updater_id', ForeignKey('users.id'))
    reporter_id: Mapped[int] = mapped_column('reporter_id', ForeignKey('users.id'))
    canceller_id: Mapped[int] = mapped_column('canceller_id', ForeignKey('users.id'))
    approver: Mapped["User"] = relationship(foreign_keys=[approver_id])
    updater: Mapped["User"] = relationship(foreign_keys=[updater_id])
    reporter: Mapped["User"] = relationship(foreign_keys=[reporter_id])
    canceller: Mapped["User"] = relationship(foreign_keys=[canceller_id])
    _value: Mapped[str] = Column('value', String(), nullable=True)
    reject_records: Mapped[List["LabRejectRecord"]] = relationship(back_populates="order_item", cascade="all, delete-orphan")

    @property
    def value(self):
        if self._value and self.test.scale == 'Quantitative':
            return float(self._value)
        else:
            return self._value

    @property
    def value_string(self):
        if self._value:
            return f'{self._value} {self.test.unit}'
        else:
            return 'N/A'

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.test.code,
            'tmlt_name': self.test.tmlt_name,
            'value': self.value,
            'comment': self.comment,
            'label': self.test.label,
            'hn': self.order.customer.hn,
            'patient': self.order.customer.fullname,
            'received_at': self.order.received_at.isoformat() if self.order.received_at else None,
            'value_string': self.value_string,
            'reported_at': self.reported_at.isoformat() if self.reported_at else None,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'cancelled_at': self.cancelled_at.isoformat() if self.cancelled_at else None,
            'approver_name': self.approver.lastname if self.approver else None,
            'reporter_name': self.reporter.lastname if self.reporter else None,
        }


class LabRejectRecord(Base):
    __tablename__ = 'lab_reject_records'
    id: Mapped[int] = Column(Integer(), autoincrement=True, primary_key=True)
    order_item_id: Mapped[int] = Column('order_item_id', ForeignKey('lab_order_items.id'))
    order_item: Mapped["LabOrderItem"] = relationship(back_populates='reject_records')


configure_mappers()

def initialize_db():
    Base.metadata.create_all(engine)
    print('Populating a default admin account...')
    user = User(firstname='Jane', lastname='Doe', username='jane', email='jane@labtycoon.com')
    user.password = '1234'
    for role in ['admin', 'approver', 'reporter']:
        r = UserRole(role_need=role)
        user.roles.append(r)

    with Session(engine) as session:
        session.add(user)
        session.commit()

    fake = Faker()
    print('Populating customers database...')
    customers = []
    for i in range(100):
        hn = fake.random_number(digits=10)
        gender = random.choice(['male', 'female'])
        firstname = fake.first_name()
        lastname = fake.last_name()
        address = fake.address()
        dob = fake.date_of_birth()

        customers.append(Customer(hn=hn,
                                  gender=gender,
                                  firstname=firstname,
                                  lastname=lastname,
                                  address=address,
                                  dob=dob)
                         )
    with Session(engine) as session:
        session.add_all(customers)
        session.commit()
