"""
send_email.py — deliver the newsletter from the group Gmail account.

Uses Gmail SMTP with an app password (Google rejects the account's regular
password for SMTP). Requires in .env:

  SENDER_EMAIL=theweeklyfriendship@gmail.com
  GMAIL_APP_PASSWORD=<16-letter app password>
  RECIPIENTS=a@example.com, b@example.com

Usage:
  python send_email.py --test     # send a one-line test email to SENDER_EMAIL only
"""

import argparse
import logging
import os
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 465
_FROM_NAME = "The Friendship Weekly"
_PAUSE_SECONDS = 15  # between individual sends; 20 people ≈ 5 minutes


def _config() -> tuple[str, str, list[str]]:
    sender = os.getenv("SENDER_EMAIL", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    recipients = [r.strip() for r in os.getenv("RECIPIENTS", "").split(",") if r.strip()]
    missing = [k for k, v in (("SENDER_EMAIL", sender), ("GMAIL_APP_PASSWORD", password),
                              ("RECIPIENTS", recipients)) if not v]
    if missing:
        raise RuntimeError(f"Missing in .env: {', '.join(missing)}")
    if len(password) != 16 or not password.isalpha():
        raise RuntimeError("GMAIL_APP_PASSWORD should be a 16-letter Google app password, "
                           "not the account password — create one at myaccount.google.com/apppasswords")
    return sender, password, recipients


def _connect(sender: str, password: str) -> smtplib.SMTP_SSL:
    smtp = smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, timeout=30)
    # Force AUTH LOGIN: with AUTH PLAIN, Gmail sometimes drops the
    # connection on bad credentials instead of returning a 535.
    smtp.ehlo()
    smtp.user, smtp.password = sender, password
    code, resp = smtp.auth("LOGIN", smtp.auth_login, initial_response_ok=False)
    if code != 235:
        smtp.close()
        raise smtplib.SMTPAuthenticationError(code, resp)
    return smtp


def _message(sender: str, recipient: str, subject: str, text: str, html: Optional[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{_FROM_NAME} <{sender}>"
    msg["To"] = recipient
    # Missing Date / Message-ID headers are a spam signal.
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@", 1)[1])
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send(subject: str, text: str, html: Optional[str] = None, to: Optional[list[str]] = None) -> None:
    """
    Email `to`, or every address in RECIPIENTS — one message per person,
    addressed to them, with a pause between sends. Individually addressed,
    spaced-out mail is far less likely to be treated as bulk spam than one
    message BCC'd to everyone.
    """
    sender, password, recipients = _config()
    if to is not None:
        recipients = to

    sent, failed = 0, []
    smtp = _connect(sender, password)
    try:
        for i, rcpt in enumerate(recipients):
            if i:
                time.sleep(_PAUSE_SECONDS)
            msg = _message(sender, rcpt, subject, text, html)
            try:
                smtp.send_message(msg)
            except smtplib.SMTPServerDisconnected:
                # Long runs can outlive the connection; reconnect once and retry.
                smtp = _connect(sender, password)
                try:
                    smtp.send_message(msg)
                except smtplib.SMTPException as exc:
                    failed.append((rcpt, exc))
                    continue
            except smtplib.SMTPException as exc:
                failed.append((rcpt, exc))
                continue
            sent += 1
            logger.info("  %d/%d sent to %s", i + 1, len(recipients), rcpt)
    finally:
        try:
            smtp.quit()
        except smtplib.SMTPException:
            pass

    for rcpt, exc in failed:
        logger.warning("Failed to send to %s: %s", rcpt, exc)
    logger.info("Sent %r to %d of %d recipient(s)", subject, sent, len(recipients))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                        help="send a test email to SENDER_EMAIL only (never the whole list)")
    args = parser.parse_args()

    if not args.test:
        sys.exit("Newsletter rendering isn't built yet — use --test to check email delivery.")

    try:
        sender, _, _ = _config()
        send(
            "friendTracker test email",
            "If you're reading this, friendTracker can send email from the group account.\n",
            to=[sender],
        )
    except smtplib.SMTPAuthenticationError as exc:
        sys.exit(f"Gmail rejected the login ({exc.smtp_code}). Check GMAIL_APP_PASSWORD in .env.")


if __name__ == "__main__":
    main()
