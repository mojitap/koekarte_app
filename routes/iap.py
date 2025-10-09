# routes/iap.py
import os, json, base64, time, requests, datetime as dt
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app_instance import db
from models import User

# 環境フラグ
BILLING_ENABLED = os.getenv("BILLING_ENABLED", "0").lower() in ("1","true","yes")

# Blueprint は定義だけ（register は app.py 側で 1 回だけ）
iap_bp = Blueprint("iap", __name__)

if BILLING_ENABLED:
    @iap_bp.before_app_request
    def _iap_debug_in():
        if request.path.startswith("/api/iap/"):
            try:
                body = request.get_json(silent=True) or {}
                keys = list(body.keys())
            except Exception:
                keys = []
            print(f"[IAP IN] {request.method} {request.path} "
                  f"uid={getattr(current_user,'id',None)} keys={keys}")

    @iap_bp.after_app_request
    def _iap_debug_out(resp):
        if request.path.startswith("/api/iap/"):
            print(f"[IAP OUT] {request.method} {request.path} -> {resp.status_code}")
        return resp
    
# ===== 設定（環境変数） =========================================
# 複数SKUをカンマ区切りで許容: IAP_PRODUCT_ID="com.koekarte.premium,com.koekarte.premium.monthly"
PRODUCT_IDS = {s.strip() for s in (os.getenv("IAP_PRODUCT_ID") or "com.koekarte.premium").split(",") if s.strip()}

# Android パッケージ名
PACKAGE = os.getenv("ANDROID_PACKAGE_NAME")  # 例: com.koekarte.app

# iOS shared secret（自動継続サブスクは必須）
APPLE_SHARED_SECRET = os.getenv("APPLE_SHARED_SECRET")

# Google Play サービスアカウント JSON（プレーン or base64 のどちらでも）
GOOGLE_SA_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# ===== 共通: DB 反映ユーティリティ ===============================
def _commit_subscription_state(user: User, platform: str, expiry_ms: int | None, order_id: str | None):
    """
    expiry_ms: サブスクの有効期限（ミリ秒, UTC）。None/0 の場合は不明扱い。
    """
    now_ms = int(time.time() * 1000)
    active = bool(expiry_ms and expiry_ms > now_ms)

    # paid_until を datetime(UTC) へ
    paid_until = None
    if expiry_ms:
        try:
            paid_until = dt.datetime.fromtimestamp(expiry_ms / 1000.0, tz=dt.timezone.utc)
        except Exception:
            paid_until = None

    user.is_paid = active
    user.has_ever_paid = True
    # モデルにフィールドがあれば更新（無ければ無視）
    try: setattr(user, "plan_status", "active" if active else "expired")
    except Exception: pass
    try: setattr(user, "paid_until", paid_until)
    except Exception: pass
    try: setattr(user, "paid_platform", platform)
    except Exception: pass

    # 取引ID等（あれば）
    if platform == "ios":
        try: setattr(user, "apple_original_tx_id", order_id or "")
        except Exception: pass
    elif platform == "android":
        try: setattr(user, "google_order_id", order_id or "")
        except Exception: pass

    db.session.commit()

    tail = (order_id or "")[-8:]
    print(f"[IAP {platform.upper()}] uid={user.id} active={active} paid_until={paid_until} order_tail={tail}")
    return active, paid_until

# ===== Android: 検証 ============================================
def _load_sa_credentials():
    # 遅延 import（有効時のみロード）
    from google.oauth2 import service_account
    if not GOOGLE_SA_JSON:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    try:
        if GOOGLE_SA_JSON.strip().startswith("{"):
            info = json.loads(GOOGLE_SA_JSON)
        else:
            info = json.loads(base64.b64decode(GOOGLE_SA_JSON).decode("utf-8"))
    except Exception:
        # 最後の手段: パス指定（/opt/render/... など）
        with open(GOOGLE_SA_JSON, "r", encoding="utf-8") as f:
            info = json.load(f)
    return service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/androidpublisher"]
    )

def _gplay_service():
    # 遅延 import（有効時のみロード）
    from googleapiclient.discovery import build
    creds = _load_sa_credentials()
    return build("androidpublisher", "v3", credentials=creds, cache_discovery=False)

def _verify_android_sub(product_id: str, purchase_token: str):
    svc = _gplay_service()
    sub = svc.purchases().subscriptions().get(
        packageName=PACKAGE, subscriptionId=product_id, token=purchase_token
    ).execute()

    expiry_ms = int(sub.get("expiryTimeMillis", "0") or 0)
    is_active = expiry_ms > int(time.time() * 1000)

    # acknowledgementState=0 なら ACK しておく（例外は握りつぶし）
    try:
        if sub.get("acknowledgementState", 0) == 0:
            svc.purchases().subscriptions().acknowledge(
                packageName=PACKAGE, subscriptionId=product_id, token=purchase_token,
                body={"developerPayload": "server-ack"}
            ).execute()
    except Exception as e:
        print(f"[IAP ANDROID] ack warn: {e}")

    return is_active, {
        "orderId": sub.get("orderId"),
        "expiry": expiry_ms,
    }

# ===== iOS: 検証 ================================================
def _verify_apple(receipt_b64: str, product_id: str | None):
    body = {
        "receipt-data": receipt_b64,
        "exclude-old-transactions": True
    }
    if APPLE_SHARED_SECRET:
        body["password"] = APPLE_SHARED_SECRET

    def call(url):
        r = requests.post(url, json=body, timeout=12)
        r.raise_for_status()
        return r.json()

    j = call("https://buy.itunes.apple.com/verifyReceipt")
    if j.get("status") == 21007:
        j = call("https://sandbox.itunes.apple.com/verifyReceipt")
    if j.get("status") != 0:
        return False, {"reason": f"apple_status_{j.get('status')}"}

    items = j.get("latest_receipt_info") or j.get("receipt", {}).get("in_app", []) or []
    # 許可SKUのみに絞る
    candidates = [it for it in items if (it.get("product_id") in PRODUCT_IDS)]
    if product_id:
        candidates = [it for it in candidates if it.get("product_id") == product_id]
    if not candidates:
        return False, {"reason": "product_not_found"}

    # expires_date_ms があればそれ、無ければ purchase_date_ms で最大
    latest = max(
        candidates,
        key=lambda it: int(it.get("expires_date_ms") or it.get("purchase_date_ms") or "0")
    )

    expires_ms = int(latest.get("expires_date_ms", "0") or 0)
    is_active = expires_ms > int(time.time() * 1000)
    order_id = latest.get("original_transaction_id") or latest.get("transaction_id")
    return is_active, {"orderId": order_id, "expiry": expires_ms}

# ===== 受け口（クライアントはここを叩く） =======================
@iap_bp.post("/verify")
@login_required
def iap_verify():
    # 無料モードでは常に成功（何もせず返す）
    if not BILLING_ENABLED:
        return jsonify({"ok": True, "status": "disabled"}), 200

    data = request.get_json(force=True) or {}
    platform    = (data.get("platform") or "").lower()  # "ios" or "android"
    product_id  = (data.get("productId") or "").strip() or None

    # SKU ホワイトリスト（productId が来た場合のみチェック）
    if product_id and (product_id not in PRODUCT_IDS):
        return jsonify({"ok": False, "error": "unknown_product"}), 400

    if platform == "android":
        purchase_token = (data.get("purchaseToken") or "").strip()
        if not (PACKAGE and product_id and purchase_token):
            return jsonify({"ok": False, "error": "missing_fields"}), 400

        try:
            ok, info = _verify_android_sub(product_id, purchase_token)
        except Exception as e:
            print(f"[IAP ANDROID] verify error: {e}")
            return jsonify({"ok": False, "error": "verify_error"}), 502

        if not ok:
            return jsonify({"ok": False, "error": "expired_or_invalid"}), 422

        # DB 反映
        active, paid_until = _commit_subscription_state(
            current_user, "android", info.get("expiry"), info.get("orderId")
        )
        return jsonify({
            "ok": True,
            "platform": "android",
            "premium": bool(active),
            "is_paid": bool(current_user.is_paid),
            "plan_status": getattr(current_user, "plan_status", None),
            "paid_until": paid_until.isoformat() if paid_until else None,
            "orderId": info.get("orderId"),
        }), 200

    if platform == "ios":
        receipt_b64 = (data.get("receipt") or "").strip()
        if not receipt_b64:
            return jsonify({"ok": False, "error": "missing_receipt"}), 400

        try:
            ok, info = _verify_apple(receipt_b64, product_id)
        except Exception as e:
            print(f"[IAP IOS] verify error: {e}")
            return jsonify({"ok": False, "error": "verify_error"}), 502

        if not ok:
            return jsonify({"ok": False, "error": info.get("reason")}), 422

        # DB 反映
        active, paid_until = _commit_subscription_state(
            current_user, "ios", info.get("expiry"), info.get("orderId")
        )
        return jsonify({
            "ok": True,
            "platform": "ios",
            "premium": bool(active),
            "is_paid": bool(current_user.is_paid),
            "plan_status": getattr(current_user, "plan_status", None),
            "paid_until": paid_until.isoformat() if paid_until else None,
            "orderId": info.get("orderId"),
        }), 200

    return jsonify({"ok": False, "error": "unsupported_platform"}), 400
