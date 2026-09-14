"""
services/notification_service.py — provider-agnostic notification dispatch.

Kept deliberately thin: swapping the SMS/email vendor (e.g. Africa's
Talking, an SMTP provider) means changing only this file, never the
callers in routes/auth_routes.py.

Email is wired to Resend (resend.com) — free tier, no credit card, and it
ships with a working onboarding@resend.dev sender so emails work
immediately without first verifying a custom sending domain. SMS stays a
stub (see _send_sms) until a provider is chosen for it.
"""

import logging
import os

import httpx

log = logging.getLogger("farmwise.notifications")

RESEND_API_URL = "https://api.resend.com/emails"


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
    api_key = os.getenv("EMAIL_PROVIDER_API_KEY", "").strip()
    if not api_key:
        log.warning(
            "email_dispatch_unconfigured email=%s subject=%s — no EMAIL_PROVIDER_API_KEY set, "
            "email NOT sent. Get a free key at resend.com and add it to Render's environment.",
            email, subject,
        )
        return

    sender = os.getenv("EMAIL_FROM", "FarmWise AI <onboarding@resend.dev>")

    try:
        res = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": sender, "to": [email], "subject": subject, "text": body},
            timeout=15.0,
        )
        res.raise_for_status()
        log.info("email_dispatch_sent email=%s subject=%s", email, subject)
    except httpx.HTTPStatusError as exc:
        # Never let a broken email provider break the request that
        # triggered it — the caller already gives a generic response
        # regardless, so a delivery failure here should be loud in logs,
        # not in the response.
        log.error(
            "email_dispatch_failed email=%s status=%s body=%s",
            email, exc.response.status_code, exc.response.text[:300],
        )
    except httpx.HTTPError as exc:
        log.error("email_dispatch_network_error email=%s error=%s", email, exc)
