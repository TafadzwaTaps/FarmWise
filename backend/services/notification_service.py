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

    default_sender = "FarmWise AI <onboarding@resend.dev>"
    sender = os.getenv("EMAIL_FROM", "").strip() or default_sender

    _dispatch_email(email, subject, body, sender, api_key)


def _dispatch_email(email: str, subject: str, body: str, sender: str, api_key: str, *, _is_fallback_attempt: bool = False) -> None:
    default_sender = "FarmWise AI <onboarding@resend.dev>"
    try:
        res = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": sender, "to": [email], "subject": subject, "text": body},
            timeout=15.0,
        )
        res.raise_for_status()
        log.info("email_dispatch_sent email=%s subject=%s sender=%s%s", email, subject, sender,
                  " (fallback sender — see previous log line)" if _is_fallback_attempt else "")
    except httpx.HTTPStatusError as exc:
        body_text = exc.response.text[:300]
        log.error(
            "email_dispatch_failed email=%s status=%s body=%s",
            email, exc.response.status_code, body_text,
        )
        # A custom EMAIL_FROM domain that isn't (yet) verified in Resend's
        # dashboard fails every single send with a 403 until DNS records
        # are added there — silently losing every password-reset/OTP email
        # in the meantime. Retry once with Resend's zero-setup testing
        # address, which needs no domain verification at all, so delivery
        # still succeeds (just from a less-branded sender) while the real
        # fix (verifying the domain at resend.com/domains) gets sorted out
        # separately. Only retries for exactly this failure mode — not
        # blindly on any 403 (e.g. a bad API key is also a 403, and
        # retrying with a different "from" address wouldn't fix that).
        if (
            not _is_fallback_attempt
            and sender != default_sender
            and exc.response.status_code == 403
            and "domain is not verified" in body_text.lower()
        ):
            log.warning(
                "email_dispatch_retrying_with_fallback_sender email=%s original_sender=%s — "
                "verify your domain at https://resend.com/domains to stop needing this fallback",
                email, sender,
            )
            _dispatch_email(email, subject, body, default_sender, api_key, _is_fallback_attempt=True)
    except httpx.HTTPError as exc:
        log.error("email_dispatch_network_error email=%s error=%s", email, exc)
