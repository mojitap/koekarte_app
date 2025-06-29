# utils/log_utils.py

from models import db, ActionLog
from flask_login import current_user

def add_action_log(admin_email, action, user_email=None):
    log = ActionLog(
        admin_email=admin_email,
        user_email=user_email or admin_email,
        action=action
    )
    db.session.add(log)
    db.session.commit()
