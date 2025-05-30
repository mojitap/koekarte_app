from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_login import current_user
from flask import redirect, url_for
from models import User  # あなたのUserモデル

ADMIN_EMAIL = 'ta714kadvance@gmail.com'  # ← あなた専用のアドレスに変更

class MyAdminIndexView(AdminIndexView):
    @expose('/')
    def index(self):
        if not current_user.is_authenticated or not current_user.email == ADMIN_EMAIL:
            return redirect(url_for('login'))
        return super(MyAdminIndexView, self).index()

class AdminModelView(ModelView):
    def is_accessible(self):
        return current_user.is_authenticated and current_user.email == ADMIN_EMAIL

    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for('login'))

def init_admin(app, db):
    admin = Admin(app, index_view=MyAdminIndexView(), template_mode='bootstrap4')
    admin.add_view(AdminModelView(User, db.session))
