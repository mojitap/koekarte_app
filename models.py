from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    username = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(200), nullable=False)

    # ✅ 新規に追加するカラム
    birthdate = db.Column(db.Date)
    gender = db.Column(db.String(50))
    occupation = db.Column(db.String(100))
    prefecture = db.Column(db.String(100))

    is_verified = db.Column(db.Boolean, default=False)
    is_paid = db.Column(db.Boolean, default=False)
    is_free_extended = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    score_logs = db.relationship('ScoreLog', backref='user', lazy=True)

    @property
    def is_admin(self):
        return self.email == 'ta714kadvance@gmail.com'


class ScoreLog(db.Model):
    __tablename__ = 'score_log'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    score = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime, default=db.func.now())
