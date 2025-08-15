# server/mailers.py
import os
from flask import current_app as app
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail as SGMail,
    Email as SGEmail,
    To as SGTo,
    ReplyTo as SGReplyTo,
)
from flask_mailman import EmailMessage as SMTPMessage  # 既存SMTPのフォールバックで使用


def send_contact_via_sendgrid(name: str, email: str, message: str) -> None:
    to_addr   = app.config.get("CONTACT_RECIPIENT", "support@koekarte.com")
    from_addr = app.config.get("MAIL_DEFAULT_SENDER", "noreply@koekarte.com")
    api_key   = os.getenv("SENDGRID_API_KEY")

    # ▼ SendGrid が未設定なら SMTP で送る（既存運用のフォールバック）
    if not api_key:
        msg = SMTPMessage(
            subject="【koekarte】お問い合わせ",
            body=f"【お問い合わせ】\n名前: {name}\nメール: {email}\n\n内容:\n{message}\n",
            to=[to_addr],
            from_email=from_addr,
            headers={"Reply-To": email},
        )
        msg.send()
        return

    # ▼ SendGrid 送信
    mail = SGMail(
        from_email=SGEmail(from_addr, name="koekarte"),
        to_emails=[SGTo(to_addr, name="Support")],
        subject="【koekarte】お問い合わせ",
        html_content=(
            f"<p><b>名前:</b> {name}</p>"
            f"<p><b>メール:</b> {email}</p>"
            f"<pre style='white-space: pre-wrap; font-family: system-ui, sans-serif;'>{message}</pre>"
        ),
    )
    mail.reply_to = SGReplyTo(email, name=(name or email))

    # ここでは例外は上位（route）に投げてOK：/api/contact 側で try/except 済み
    SendGridAPIClient(api_key).send(mail)
