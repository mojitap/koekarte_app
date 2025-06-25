# utils/auth_utils.py
from datetime import date
import os
from dotenv import load_dotenv

load_dotenv()

def get_user_plan_status(user):
    today = date.today()
    created_at = user.created_at.date() if user.created_at else today
    days_passed = (today - created_at).days
    allowed_emails = os.getenv("ALLOWED_FREE_EMAILS", "").split(",")

    is_paid = user.is_paid
    is_free_extended = (
        user.is_free_extended or
        user.email in allowed_emails or
        days_passed < 5
    )
    can_use_premium = is_paid or is_free_extended

    return {
        'is_paid': is_paid,
        'is_free_extended': is_free_extended,
        'can_use_premium': can_use_premium,
        'days_since_signup': days_passed
    }

def check_can_use_premium(user):
    return get_user_plan_status(user).get("can_use_premium", False)
