"""
services/notification_service.py — provider-agnostic notification dispatch.

Kept deliberately thin: swapping the SMS/email vendor (e.g. Africa's
Talking, an SMTP provider) means changing only this file, never the
callers in routes/auth_routes.py.
"""

import logging
import os

log = logging.getLogger("farmwise.notifications")


def send_otp(destination: str, code: str) -> None:
    if "@" in destination:
        _send_email(destination, "Your FarmWise AI code", f"Your verification code is {code}")
    else:
        _send_sms(destination, f"Your FarmWise AI code is {code}")


def send_push(user_id: str, title: str, body: str) -> None:
    log.info("push_notification user_id=%s title=%s body=%s", user_id, title, body)
    # TODO: integrate Expo push notifications / FCM


def _send_sms(phone_number: str, message: str) -> None:
    configured = bool(os.getenv("SMS_PROVIDER_API_KEY"))
    log.info("sms_dispatch phone=%s configured=%s message=%s", phone_number, configured, message)
    # TODO: integrate SMS provider (e.g. Africa's Talking)


def _send_email(email: str, subject: str, body: str) -> None:
    configured = bool(os.getenv("EMAIL_PROVIDER_API_KEY"))
    log.info("email_dispatch email=%s subject=%s configured=%s", email, subject, configured)
    # TODO: integrate transactional email provider
