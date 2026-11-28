"""Email sending for agents. Settings come only from .env (SMTP_*)."""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Protocol


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str, *, reply_to: str | None = None, from_name: str = "",
             attachments: list | None = None) -> str: ...


class SmtpMailer:
    def __init__(self):
        self.host = os.getenv("SMTP_HOST", "")
        self.port = int(os.getenv("SMTP_PORT", "587") or 587)
        self.user = os.getenv("SMTP_USER", "")
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.use_ssl = os.getenv("SMTP_SSL", "false").lower() == "true"
        self.sender = os.getenv("SMTP_FROM", self.user)

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    def send(self, to: str, subject: str, body: str, *, reply_to: str | None = None, from_name: str = "",
             attachments: list | None = None) -> str:
        if not self.configured:
            raise RuntimeError("Email sending isn't set up on the server yet (SMTP settings)")
        msg = EmailMessage()
        msg["From"] = formataddr((from_name or "Goal Engine", self.sender))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=self.sender.split("@")[-1])
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body)
        for filename, data, mime in attachments or []:
            maintype, subtype = mime.split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        context = ssl.create_default_context()
        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30) as smtp:
                smtp.login(self.user, self.password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
                smtp.starttls(context=context)
                smtp.login(self.user, self.password)
                smtp.send_message(msg)
        return str(msg["Message-ID"])
