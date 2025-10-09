# utils/subscription_utils.py
import os
from datetime import datetime, timezone

BILLING_ENABLED = os.getenv("BILLING_ENABLED", "0").lower() in ("1","true","yes")
if BILLING_ENABLED:
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def sync_subscription_from_stripe(user):
    from app_instance import db  # ←OK（関数内importで循環回避）

    # 無料モード：Stripe を一切触らず成功扱いで戻す
    if not BILLING_ENABLED:
        return True, "disabled"

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

    # 2) サブスク取得（active / trialing を優先）
    subs = stripe.Subscription.list(customer=cust_id, status="all", limit=10)
    active_sub = next((s for s in subs.auto_paging_iter() if s.status in ("active", "trialing")), None)

    if not active_sub:
        user.is_paid = False
        user.plan_status = None
        user.stripe_subscription_id = None
        user.current_period_end = None
        # ここを追加（落とし忘れ防止）
        user.paid_until = None
        user.paid_platform = None
        db.session.commit()
        return True, "none"

    # サブスク有り → 反映
    user.is_paid = True
    user.has_ever_paid = True
    user.plan_status = active_sub.status
    user.stripe_subscription_id = active_sub.id
    user.current_period_end = datetime.fromtimestamp(active_sub.current_period_end, tz=timezone.utc)
    user.paid_until = user.current_period_end           # ← 既存ガード互換のためここを更新
    user.paid_platform = 'web'
    db.session.commit()
    return True, active_sub.status
