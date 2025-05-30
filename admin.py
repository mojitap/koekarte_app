from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_login import current_user
from flask import redirect, url_for
from models import User  # あなたのUserモデル

# ── 管理者アクセス制限 ──
class MyAdminIndexView(AdminIndexView):
    @expose('/')
    def index(self):
        if not current_user.is_authenticated or not current_user.email == 'ta714kadvance@gmail.com':
            return redirect(url_for('login'))  # または abort(403)
        return super(MyAdminIndexView, self).index()

# ── 管理者専用のモデル表示制限 ──
class AdminModelView(ModelView):
    def is_accessible(self):
        return current_user.is_authenticated and current_user.email == 'ta714kadvance@gmail.com'

    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for('login'))

def init_admin(app, db):
    admin = Admin(app, index_view=MyAdminIndexView(), template_mode='bootstrap4')
    admin.add_view(AdminModelView(User, db.session))
