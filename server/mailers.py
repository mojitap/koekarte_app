# server/mailers.py
import os
from flask import current_app as app
from flask_mailman import EmailMessage  # 既存SMTPフォールバックで使用

def _send_via_smtp(name: str, email: str, message: str) -> None:
    to_addr   = app.config.get('CONTACT_RECIPIENT', 'support@koekarte.com')
    from_addr = app.config.get('MAIL_DEFAULT_SENDER', 'noreply@koekarte.com')
    msg = EmailMessage(
        subject="【koekarte】お問い合わせ",
        body=f"【お問い合わせ】\n名前: {name}\nメール: {email}\n\n内容:\n{message}\n",
        to=[to_addr],
        from_email=from_addr,
        headers={'Reply-To': email}
    )
    msg.send()

def send_contact_via_sendgrid(name: str, email: str, message: str) -> None:
    """SendGrid が使えれば SendGrid で送信。無ければ SMTP にフォールバック。"""
    api_key = os.getenv('SENDGRID_API_KEY')
    if not api_key:
        app.logger.info("SENDGRID_API_KEY 未設定のため SMTP 送信にフォールバックします。")
        return _send_via_smtp(name, email, message)

    try:
        # 起動時に SDK が無くても落ちないように遅延 import
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, ReplyTo
    except Exception as e:
        app.logger.warning("SendGrid SDK の import に失敗。SMTP にフォールバック: %r", e)
        return _send_via_smtp(name, email, message)

    to_addr   = app.config.get('CONTACT_RECIPIENT', 'support@koekarte.com')
    from_addr = app.config.get('MAIL_DEFAULT_SENDER', 'noreply@koekarte.com')

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
        SendGridAPIClient(api_key).send(mail)
    except Exception:
        app.logger.exception("SendGrid 送信に失敗。SMTP にフォールバックします。")
        _send_via_smtp(name, email, message)

# 互換エイリアス（app.py を直さない場合の保険）
send_contact = send_contact_via_sendgrid
