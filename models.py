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

    # ★ここを追加★  ------------------------------------
    last_score      = db.Column(db.Integer)      # 直近スコア
    last_recorded   = db.Column(db.DateTime)     # 直近録音日時
    # ---------------------------------------------------

    # ✅ 管理者判定
    @property
    def is_admin(self):
        return self.email == 'ta714kadvance@gmail.com'

    # ✅ Flask-Loginのために必要（Trueを返さないとセッションが無効になる）
    @property
    def is_active(self):
        return True

class ScoreLog(db.Model):
    __tablename__ = 'score_log'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    score = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime, default=db.func.now())
    is_fallback = db.Column(db.Boolean, default=False)

class ActionLog(db.Model):
    __tablename__ = 'action_log'
    id = db.Column(db.Integer, primary_key=True)
    admin_email = db.Column(db.String(150))  # 操作した管理者
    user_email = db.Column(db.String(150))   # 対象ユーザー
    action = db.Column(db.String(100))       # 内容（例: 有料に変更）
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class ScoreFeedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    internal = db.Column(db.Float, nullable=False)
    user_score = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
