from datetime import date

def check_can_use_premium(user):
    if user.is_paid:
        return True
    if user.is_free_extended:
        return True
    if user.has_ever_paid:
        # 一度でも有料登録した人は、再度課金しないと無料期間に戻らない
        return False
    if not user.created_at:
        return False
    days_passed = (date.today() - user.created_at.date()).days
    return days_passed < 5
