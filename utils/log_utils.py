from models import db, ActionLog, User
from flask_login import current_user

def add_action_log(admin_email, action, user_email=None, user_id=None):
    # 安全対策：user_emailがない場合、user_idから引き直す
    if not user_email and user_id:
        user = User.query.get(user_id)
        if user:
            user_email = user.email

    log = ActionLog(
        admin_email=admin_email,
        user_email=user_email or admin_email,
        action=action
    )
    db.session.add(log)
    db.session.commit()
