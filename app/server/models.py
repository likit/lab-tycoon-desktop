from server.extensions import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer(), auto_increment=True, primary_key=True)
    username = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    hashed_password = db.Column(db.String(255), nullable=False)
    firstname = db.Column('firstname', db.String(255), nullable=False)
    lastname = db.Column('firstname', db.String(255), nullable=False)
    license_id = db.Column('license_id', db.String(255), nullable=True)

    @property
    def password(self):
        raise ValueError('Password is not accessible.')

    @password.setter
    def password(self, new_password):
        self.hashed_password = generate_password_hash(new_password)

    def check_password(self, password):
        return check_password_hash(self.hashed_password, password)
