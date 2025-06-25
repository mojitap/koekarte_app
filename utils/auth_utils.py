from datetime import date

def check_can_use_premium(user):
    if user.is_paid:
        return True
    if user.is_free_extended:
        return True
    if not user.created_at:
        return False
    days_passed = (date.today() - user.created_at.date()).days
    return days_passed < 5
