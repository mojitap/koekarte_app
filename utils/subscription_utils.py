# utils/subscription_utils.py
import os, stripe
from datetime import datetime, timezone

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def sync_subscription_from_stripe(user):
    from app import db  # 循環import回避のため遅延import

    cust_id = getattr(user, "stripe_customer_id", None)
    cust = None

    # 1) 顧客IDが無ければメールから探索して保存
    if not cust_id:
        try:
            r = stripe.Customer.search(query=f"email:'{user.email}'")
            if r.data:
                cust = r.data[0]
        except Exception:
            r = stripe.Customer.list(email=user.email, limit=1)
            if r.data:
                cust = r.data[0]

        if cust:
            user.stripe_customer_id = cust.id
            db.session.commit()
            cust_id = cust.id
        else:
            return False, "no_customer"

    # 2) サブスク取得 → active/trialing を優先
    subs = stripe.Subscription.list(customer=cust_id, status="all", limit=10)
    active_sub = next((s for s in subs.auto_paging_iter()
                       if s.status in ("active", "trialing")), None)

    if not active_sub:
        user.is_paid = False
        user.plan_status = None
        user.stripe_subscription_id = None
        user.current_period_end = None
        db.session.commit()
        return True, "none"

    user.is_paid = True
    user.has_ever_paid = True
    user.plan_status = active_sub.status
    user.stripe_subscription_id = active_sub.id
    user.current_period_end = datetime.fromtimestamp(
        active_sub.current_period_end, tz=timezone.utc
    )
    db.session.commit()
    return True, active_sub.status
