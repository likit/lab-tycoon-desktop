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


class Test(db.Model):
    __tablename__ = 'tests'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    code = db.Column('code', db.String(), nullable=False)
    label = db.Column('label', db.String(), nullable=False)
    value_type = db.Column('value_type', db.String(), nullable=False)


class TestItem(db.Model):
    __tablename__ = 'test_items'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    test_id = db.Column(db.ForeignKey('tests.id'))
    specimens_id = db.Column(db.ForeignKey('specimens.id'))
    price = db.Column('price', db.Numeric(), default=0.0, nullable=False)
    desc = db.Column('desc', db.Text())
    test = db.relationship(Test, backref=db.backref('items', lazy='dynamic', cascade='all, delete-orphan'))
    unit = db.Column('unit', db.String(), nullable=False)


class TestRecord(db.Model):
    __tablename__ = 'test_records'
    id = db.Column(db.Integer(), autoincrement=True, primary_key=True)
    test_item_id = db.Column(db.ForeignKey('test_items.id'))
    _value = db.Column('value', db.String(), nullable=True)
    test_item = db.relationship(TestItem, backref=db.backref('records', lazy='dynamic', cascade='all, delete-orphan'))

    @property
    def value(self):
        if self.test_item.test.value_type == 'numeric':
            return float(self._value)
        else:
            return self._value

    @property
    def value_string(self):
        return f'{self._value} {self.unit}'
