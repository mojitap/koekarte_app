from datetime import datetime, date
from config import ALLOWED_FREE_EMAILS  # ← 必要に応じて import

def get_user_plan_status(user):
    today = date.today()
    created_at = user.created_at.date() if user.created_at else today
    days_passed = (today - created_at).days

    is_paid = user.is_paid
    is_free_extended = (
        user.is_free_extended or
        user.email in ALLOWED_FREE_EMAILS or
        days_passed < 5
    )
    can_use_premium = is_paid or is_free_extended

    return {
        'is_paid': is_paid,
        'is_free_extended': is_free_extended,
        'can_use_premium': can_use_premium,
        'days_since_signup': days_passed
    }
