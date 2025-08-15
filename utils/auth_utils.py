# server/utils/auth_utils.py
import os
from datetime import datetime, timedelta, timezone

FREE_DAYS = int(os.getenv("FREE_TRIAL_DAYS", "7"))

def check_can_use_premium(user):
    now = datetime.now(timezone.utc)

    paid_until = getattr(user, "paid_until", None)
    if paid_until:
        if paid_until.tzinfo is None:
            paid_until = paid_until.replace(tzinfo=timezone.utc)
        if paid_until >= now:
            return True, "paid"

    if getattr(user, "is_free_extended", False):
        return True, "extended"

    created_at = getattr(user, "created_at", None)
    if created_at:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at + timedelta(days=FREE_DAYS) >= now:
            return True, "trial"

    return False, "free"
