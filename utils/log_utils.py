# utils/log_utils.py

from models import db, ActionLog

def add_action_log(user_id, action):
    log = ActionLog(user_id=user_id, action=action)
    db.session.add(log)
    db.session.commit()
