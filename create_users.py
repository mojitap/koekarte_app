from datetime import datetime
from werkzeug.security import generate_password_hash
from app import app, db
from models import User

with app.app_context():
    # 既に同じメールのユーザーがいないか確認
    existing_user = User.query.filter_by(email='admin@example.com').first()
    if existing_user:
        print("⚠️ すでに admin@example.com は存在します。作成をスキップします。")
    else:
        user = User(
            email='ta714kadvance@gmail.com',
            username='管理者',
            password=generate_password_hash('your_secure_password'),
            is_verified=True,
            is_paid=True,
            is_free_extended=True
        )
        db.session.add(user)
        db.session.commit()
        print("✅ 管理者ユーザー admin@example.com を作成しました。")
