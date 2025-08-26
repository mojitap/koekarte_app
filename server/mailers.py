# server/mailers.py
import os
from flask import current_app as app
from flask_mailman import EmailMessage

def _send_via_smtp(name: str, email: str, message: str) -> None:
    to_addr   = app.config.get('CONTACT_RECIPIENT', app.config.get('MAIL_DEFAULT_SENDER'))
    from_addr = app.config.get('MAIL_DEFAULT_SENDER')
    app.logger.info(f"[SMTP] from={from_addr} to={to_addr} reply_to={email}")
    msg = EmailMessage(
        subject="【koekarte】お問い合わせ",
        body=f"【お問い合わせ】\n名前: {name}\nメール: {email}\n\n内容:\n{message}\n",
        to=[to_addr],
        from_email=from_addr,
        headers={'Reply-To': email}
    )
    msg.send()

def send_contact_via_sendgrid(name: str, email: str, message: str) -> None:
    api_key = os.getenv('SENDGRID_API_KEY')
    force_smtp = os.getenv('SENDGRID_FORCE_SMTP', '0') == '1'

    if not api_key or force_smtp:
        app.logger.info("[Mailer] Using SMTP (no API key or forced).")
        return _send_via_smtp(name, email, message)

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, ReplyTo
    except Exception as e:
        app.logger.warning(f"[Mailer] SendGrid import failed: {e} -> fallback SMTP")
        return _send_via_smtp(name, email, message)

    to_addr   = app.config.get('CONTACT_RECIPIENT', app.config.get('MAIL_DEFAULT_SENDER'))
    from_addr = app.config.get('MAIL_DEFAULT_SENDER')
    app.logger.info(f"[SendGrid] from={from_addr} to={to_addr} reply_to={email}")

    mail = Mail(
        from_email=Email(from_addr, name="koekarte"),
        to_emails=[To(to_addr, name="Support")],
        subject="【koekarte】お問い合わせ",
        html_content=f"""
            <p><b>名前:</b> {name}</p>
            <p><b>メール:</b> {email}</p>
            <pre style="white-space: pre-wrap; font-family: system-ui, sans-serif;">{message}</pre>
        """,
    )
    mail.reply_to = ReplyTo(email, name=name or email)

    try:
        resp = SendGridAPIClient(api_key).send(mail)
        app.logger.info(f"[SendGrid] status={resp.status_code}")
        # 配信に失敗したら SMTP にフォールバック（保険）
        if resp.status_code >= 400:
            app.logger.error(f"[SendGrid] failed with {resp.status_code}, fallback SMTP")
            _send_via_smtp(name, email, message)
    except Exception:
        app.logger.exception("[SendGrid] send failed -> fallback SMTP")
        _send_via_smtp(name, email, message)

def send_password_reset_email(to_email: str, reset_url: str) -> None:
    subject = "【koekarte】パスワード再設定のご案内"
    html = f"""
      <p>いつもご利用ありがとうございます。</p>
      <p>以下のリンクからパスワードを再設定してください（有効期限：1時間）。</p>
      <p><a href="{reset_url}">{reset_url}</a></p>
      <hr>
      <p style="color:#666;font-size:14px;">
        ※ iPhoneの方はSafari、Androidの方はChromeで開くと確実です。
      </p>
    """
    text = f"""パスワード再設定リンク（1時間有効）:
{reset_url}

※ iPhoneはSafari、AndroidはChromeでのアクセス推奨
"""

    api_key = os.getenv('SENDGRID_API_KEY')
    force_smtp = os.getenv('SENDGRID_FORCE_SMTP', '0') == '1'
    from_addr = app.config.get('MAIL_DEFAULT_SENDER')

    if not api_key or force_smtp:
        # SMTPでプレーンテキスト送信（最低限）
        msg = EmailMessage(subject=subject, body=text, to=[to_email], from_email=from_addr)
        msg.send()
        return

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To
        mail = Mail(
            from_email=Email(from_addr, name="koekarte"),
            to_emails=[To(to_email)],
            subject=subject,
            html_content=html,
            plain_text_content=text
        )
        SendGridAPIClient(api_key).send(mail)
    except Exception:
        # 失敗したらSMTPにフォールバック
        msg = EmailMessage(subject=subject, body=text, to=[to_email], from_email=from_addr)
        msg.send()

# 互換
send_contact = send_contact_via_sendgrid
