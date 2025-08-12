# routes/iap.py
import os, json, base64, time, requests
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app_instance import db
from models import User
from google.oauth2 import service_account
from googleapiclient.discovery import build

iap_bp = Blueprint("iap", __name__)

PRODUCT_IDS = set((os.getenv("IAP_PRODUCT_ID") or "com.koekarte.premium").split(","))
PACKAGE     = os.getenv("ANDROID_PACKAGE_NAME")

def _load_sa_credentials():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    try:
        if raw.strip().startswith("{"):
            info = json.loads(raw)
        else:
            info = json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception:
        # 最後の手段: パス指定
        with open(raw, "r", encoding="utf-8") as f:
            info = json.load(f)
    return service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/androidpublisher"]
    )

def _gplay_service():
    creds = _load_sa_credentials()
    return build("androidpublisher", "v3", credentials=creds, cache_discovery=False)

def verify_android_sub(product_id: str, purchase_token: str):
    svc = _gplay_service()
    sub = svc.purchases().subscriptions().get(
        packageName=PACKAGE, subscriptionId=product_id, token=purchase_token
    ).execute()
    # 有効期限
    expiry_ms = int(sub.get("expiryTimeMillis", "0"))
    is_active = expiry_ms > int(time.time() * 1000)
    # 未ACKならACK
    if sub.get("acknowledgementState", 0) == 0:
        svc.purchases().subscriptions().acknowledge(
            packageName=PACKAGE, subscriptionId=product_id, token=purchase_token,
            body={"developerPayload": "server-ack"}
        ).execute()
    return is_active, {"orderId": sub.get("orderId"), "expiry": expiry_ms}

def verify_apple(receipt_b64: str, product_id: str):
    body = {"receipt-data": receipt_b64, "exclude-old-transactions": True}
    pw = os.getenv("APPLE_SHARED_SECRET")
    if pw: body["password"] = pw

    def call(url):
        r = requests.post(url, json=body, timeout=12)
        r.raise_for_status()
        return r.json()

    j = call("https://buy.itunes.apple.com/verifyReceipt")
    if j.get("status") == 21007:
        j = call("https://sandbox.itunes.apple.com/verifyReceipt")
    if j.get("status") != 0:
        return False, {"reason": f"apple_status_{j.get('status')}"}

    # 最新のその product_id のトランザクションを拾う
    items = j.get("latest_receipt_info") or j.get("receipt", {}).get("in_app", [])
    candidates = [it for it in items if it.get("product_id") in PRODUCT_IDS]
    if not candidates:
        return False, {"reason": "product_not_found"}

    latest = max(
        candidates,
        key=lambda it: int(it.get("expires_date_ms") or it.get("purchase_date_ms") or "0")
    )
    expires_ms = int(latest.get("expires_date_ms", "0"))
    is_active = expires_ms > int(time.time() * 1000)
    return is_active, {"orderId": latest.get("original_transaction_id"), "expiry": expires_ms}

def _mark_premium(user_id: int, platform: str, order_id: str):
    user = User.query.get(user_id)
    if not user: return False
    user.is_paid = True
    user.has_ever_paid = True
    db.session.commit()
    return True

@iap_bp.post("/verify")
@login_required
def iap_verify():
    data = request.get_json(force=True) or {}
    platform   = (data.get("platform") or "").lower()
    product_id = data.get("productId")
    if product_id and product_id not in PRODUCT_IDS:
        return jsonify({"ok": False, "error": "unknown product"}), 400

    if platform == "android":
        token = data.get("purchaseToken")
        if not (PACKAGE and token and product_id):
            return jsonify({"ok": False, "error": "missing fields"}), 400
        ok, info = verify_android_sub(product_id, token)
        if not ok: return jsonify({"ok": False, "error": "expired_or_invalid"}), 422
        _mark_premium(current_user.id, "android", info["orderId"])
        return jsonify({"ok": True, "premium": True, **info})

    if platform == "ios":
        receipt = data.get("receipt")
        if not receipt: return jsonify({"ok": False, "error": "missing receipt"}), 400
        ok, info = verify_apple(receipt, product_id or next(iter(PRODUCT_IDS)))
        if not ok: return jsonify({"ok": False, "error": info.get("reason")}), 422
        _mark_premium(current_user.id, "ios", info["orderId"])
        return jsonify({"ok": True, "premium": True, **info})

    return jsonify({"ok": False, "error": "unsupported platform"}), 400