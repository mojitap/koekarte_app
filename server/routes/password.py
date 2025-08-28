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

    def ok():  # 常に成功を返す（アカウント有無を伏せる）
        return jsonify({"ok": True})

    current_app.logger.info(f"[pw-forgot] start email={email} ip={ip}")

    if not email:
        current_app.logger.info("[pw-forgot] empty email -> ok")
        return ok()

    try:
        user = User.query.filter(User.email == email).first()
        if not user:
            current_app.logger.info("[pw-forgot] user not found -> ok")
            return ok()

        token_value = secrets.token_hex(32)                         # メールに入れる生トークン
        token_hash  = hashlib.sha256(token_value.encode()).hexdigest()  # DB保存はハッシュ
        expires_at  = datetime.now(UTC) + timedelta(hours=1)

        current_app.logger.info(f"[pw-forgot] issue token for user_id={user.id}")

        # DBへ保存
        db.session.execute(text("""
            INSERT INTO password_reset_tokens
                (user_id, token_hash, expires_at, requested_ip, user_agent)
            VALUES (:uid, :th, :exp, :ip, :ua)
        """), {"uid": user.id, "th": token_hash, "exp": expires_at, "ip": ip, "ua": ua})
        db.session.commit()

        base = current_app.config.get("WEB_BASE_URL", "https://koekarte.com")
        url  = f"{base}/reset-password?token={token_value}&email={urllib.parse.quote(email)}"
        current_app.logger.info(f"[pw-forgot] send mail url={url}")

        try:
            send_password_reset_email(email, url)
        except Exception as e:
            current_app.logger.exception(f"[pw-forgot] send mail failed: {e}")  # 送信失敗でもOKを返す

        return ok()

    except Exception as e:
        current_app.logger.exception(f"[pw-forgot] server error: {e}")
        return jsonify({"error": "server_error", "success": False}), 500


@bp.post("/password/reset")
def reset():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    token = (data.get("token") or "").strip()
    new_password = data.get("new_password")

    if not token or not new_password:
        return jsonify({"error": "missing_fields"}), 400

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        # token から直接ユーザーを特定（email 省略でもOK）
        rec = db.session.execute(text("""
            SELECT id, user_id FROM password_reset_tokens
            WHERE token_hash = :th
              AND consumed_at IS NULL
              AND expires_at > now()
            ORDER BY created_at DESC
            LIMIT 1
        """), {"th": token_hash}).first()

        user = User.query.get(rec.user_id) if rec else None
        if not rec or not user:
            return jsonify({"error": "invalid_or_expired"}), 400

        user.password_hash = generate_password_hash(new_password)
        db.session.execute(text("UPDATE password_reset_tokens SET consumed_at = now() WHERE id = :id"),
                           {"id": rec.id})
        db.session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        current_app.logger.exception(f"[pw-reset] server error: {e}")
        return jsonify({"error": "server_error"}), 500
