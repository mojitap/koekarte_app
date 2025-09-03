# utils/subscription_utils.py
import os, stripe, datetime as dt
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

def sync_subscription_from_stripe(user):
    """
    Stripe から当該ユーザーのサブスク状態を取得し、User を更新して commit します。
    戻り値: (bool_succeeded, status_or_reason)
    """
    # ← ここで遅延 import にして循環 import を回避
    from app import db

    if not getattr(user, "stripe_customer_id", None):
        return False, "no_customer"

    try:
        subs = stripe.Subscription.list(
            customer=user.stripe_customer_id, status="all", limit=10
        )
        # active / trialing を最優先で拾う
        active_sub = next(
            (s for s in subs.auto_paging_iter() if s.status in ("active", "trialing")),
            None
        )
    except Exception as e:
        print(f"[SYNC ERR] {e}")
        return False, "stripe_error"

    if not active_sub:
        # サブスク無し
        user.is_paid = False
        user.plan_status = None
        user.stripe_subscription_id = None
        user.current_period_end = None
        db.session.commit()
        print("[SYNC] No active subscription -> set free")
        return True, "none"

    # サブスク有り
    user.is_paid = True
    user.has_ever_paid = True
    user.plan_status = active_sub.status
    user.stripe_subscription_id = active_sub.id
    user.current_period_end = dt.datetime.fromtimestamp(active_sub.current_period_end)
    db.session.commit()
    print(f"[SYNC] Active sub {active_sub.id} ({active_sub.status}) -> set paid")
    return True, active_sub.status
