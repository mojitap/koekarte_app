# create_users.py

from app import app, db
from models import User
from werkzeug.security import generate_password_hash

# 🔐 管理者情報（変更する場合はここを編集）
ADMIN_EMAIL    = 'admin@example.com'
ADMIN_PASSWORD = 'your_admin_password'  # ←任意の安全なパスワードに変更
ADMIN_NAME     = '管理者'

with app.app_context():
    # すでに登録済みならスキップ
    existing_user = User.query.filter_by(email=ADMIN_EMAIL).first()
    if existing_user:
        print(f"⚠️ すでに登録済み: {existing_user.email}")
    else:
        hashed_password = generate_password_hash(ADMIN_PASSWORD)
        user = User(
            email=ADMIN_EMAIL,
            password=hashed_password,
            name=ADMIN_NAME,
            is_paid=True,  # 管理者は有料ステータスでもOK
        )
        db.session.add(user)
        db.session.commit()
        print(f"✅ 管理者ユーザーを追加しました: {user.email}")