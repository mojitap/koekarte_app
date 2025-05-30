from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_login import current_user
from flask import redirect, url_for
from models import User, ActionLog  # ✅ ActionLog を忘れずに追加！

# 管理者として許可するメールアドレス
ADMIN_EMAIL = 'ta714kadvance@gmail.com'

# 管理画面のトップページにアクセス制限をかける
class MyAdminIndexView(AdminIndexView):
    @expose('/')
    def index(self):
        if not current_user.is_authenticated or not current_user.is_admin:
            return redirect(url_for('login'))
        return super().index()

# モデルごとのビューにもアクセス制限
class AdminModelView(ModelView):
    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_admin

    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for('login'))

# アプリ初期化時に Flask-Admin を有効化する関数
def init_admin(app, db):
    admin = Admin(app, name='管理画面', template_mode='bootstrap4', index_view=MyAdminIndexView())
    admin.add_view(AdminModelView(User, db.session))
    admin.add_view(AdminModelView(ActionLog, db.session))  # admin.py に追加
