"""Lightweight email notification service (§9 — notify on allocation confirm/cancel).

No-op if SMTP isn't configured (`RMS_SMTP_HOST` unset) — the same
"works out of the box, degrades gracefully" posture as the rest of this
build. Real delivery is fire-and-forget: called *after* the triggering
transaction has already committed, and a failed send is logged, never
raised, so a flaky mail server can't break a booking.
"""
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def send_notification(to: list[str] | str, subject: str, body: str) -> bool:
    """Best-effort email send.

    Returns True if a send was attempted and succeeded, False if skipped
    (SMTP not configured, or no recipients) or failed (logged, not raised).
    """
    settings = get_settings()
    if not settings.smtp_host:
        logger.info("notifications: SMTP not configured, skipping %r", subject)
        return False
    recipients = [to] if isinstance(to, str) else list(to)
    recipients = [r for r in recipients if r]
    if not recipients:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPNotSupportedError:
                pass
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception:  # pragma: no cover - best-effort, must never block the caller
        logger.warning("notifications: failed to send %r to %s", subject, recipients, exc_info=True)
        return False


def notify_allocation_confirmed(
    to_email: str | None, staff_name: str, engagement_code: str, client_name: str, date_from: str, date_to: str,
) -> bool:
    if not to_email:
        return False
    subject = f"[firm-rms] Booking confirmed: {engagement_code} ({date_from} to {date_to})"
    body = f"{staff_name} has been confirmed on {engagement_code} ({client_name}) from {date_from} to {date_to}."
    return send_notification(to_email, subject, body)


def notify_allocation_cancelled(
    to_email: str | None, staff_name: str, engagement_code: str, client_name: str, date_from: str, date_to: str,
    reason: str | None = None,
) -> bool:
    if not to_email:
        return False
    subject = f"[firm-rms] Booking cancelled: {engagement_code} ({date_from} to {date_to})"
    body = f"{staff_name}'s booking on {engagement_code} ({client_name}) from {date_from} to {date_to} has been cancelled."
    if reason:
        body += f" Reason: {reason}"
    return send_notification(to_email, subject, body)
