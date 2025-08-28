# server/routes/password.py
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy import text
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash
import hashlib, secrets, urllib.parse

from app_instance import db
from models import User
from server.mailers import send_password_reset_email

bp = Blueprint("password", __name__)
UTC = timezone.utc

@bp.post("/password/forgot")
def forgot():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    ua = request.headers.get("user-agent", "")
    ip = request.headers.get("x-forwarded-for") or request.remote_addr

    # 常に成功（アカウント存在を伏せる）
    def ok(): return jsonify({"ok": True})

    if not email:
        return ok()

    user = User.query.filter(User.email == email).first()
    if not user:
        return ok()

    # 生トークン（メール用）とハッシュ（DB保存用）
    token_value = secrets.token_hex(32)
    token_hash  = hashlib.sha256(token_value.encode()).hexdigest()
    expires_at  = datetime.now(UTC) + timedelta(hours=1)

    db.session.execute(text("""
        INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, requested_ip, user_agent)
        VALUES (:uid, :th, :exp, :ip, :ua)
    """), {"uid": user.id, "th": token_hash, "exp": expires_at, "ip": ip, "ua": ua})
    db.session.commit()

    base = current_app.config.get("WEB_BASE_URL", "https://koekarte.com")
    url  = f"{base}/reset-password?token={token_value}&email={urllib.parse.quote(email)}"
    try:
        send_password_reset_email(email, url)
    except Exception as e:
        current_app.logger.exception("send_password_reset_email failed: %s", e)
        # 送れなくてもOKを返す（悪用防止）
    return ok()


@bp.post("/password/reset")
def reset():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    token = (data.get("token") or "").strip()
    new_password = data.get("new_password")

    if not email or not token or not new_password:
        return jsonify({"error": "missing_fields"}), 400

    user = User.query.filter(User.email == email).first()
    if not user:
        return jsonify({"error": "invalid_or_expired"}), 400

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    rec = db.session.execute(text("""
        SELECT id FROM password_reset_tokens
        WHERE user_id = :uid
          AND token_hash = :th
          AND consumed_at IS NULL
          AND expires_at > now()
        ORDER BY created_at DESC
        LIMIT 1
    """), {"uid": user.id, "th": token_hash}).first()

    if not rec:
        return jsonify({"error": "invalid_or_expired"}), 400

    # 更新＆消費
    user.password_hash = generate_password_hash(new_password)
    db.session.execute(text("""
        UPDATE password_reset_tokens
           SET consumed_at = now()
         WHERE id = :id
    """), {"id": rec.id})
    db.session.commit()

    return jsonify({"ok": True})