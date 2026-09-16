"""
tests/test_notification_service.py — email dispatch, specifically the new
fallback-to-default-sender behavior when a custom EMAIL_FROM domain isn't
verified with Resend yet (a real production issue: every send silently
failed with no way to actually deliver a password-reset/OTP email until
DNS records were added at resend.com/domains).
"""

from unittest.mock import MagicMock, patch

import services.notification_service as notif


def _resend_response(status_code, body_json=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = str(body_json) if body_json else ""
    if status_code >= 400:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError("err", request=MagicMock(), response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_no_api_key_does_not_attempt_send(monkeypatch):
    monkeypatch.delenv("EMAIL_PROVIDER_API_KEY", raising=False)
    with patch("services.notification_service.httpx.post") as mocked_post:
        notif._send_email("user@example.com", "Subject", "Body")
    mocked_post.assert_not_called()


def test_custom_sender_succeeds_no_fallback(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "key")
    monkeypatch.setenv("EMAIL_FROM", "FarmWise AI <noreply@farmwiseai.app>")
    with patch("services.notification_service.httpx.post", return_value=_resend_response(200)) as mocked_post:
        notif._send_email("user@example.com", "Subject", "Body")
    assert mocked_post.call_count == 1
    assert mocked_post.call_args[1]["json"]["from"] == "FarmWise AI <noreply@farmwiseai.app>"


def test_unverified_domain_falls_back_to_default_sender_and_succeeds(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "key")
    monkeypatch.setenv("EMAIL_FROM", "FarmWise AI <noreply@farmwiseai.app>")
    unverified_error = _resend_response(403, {"statusCode": 403, "message": "The farmwiseai.app domain is not verified. Please, add and verify your domain on https://resend.com/domains", "name": "validation_error"})
    unverified_error.text = '{"statusCode":403,"message":"The farmwiseai.app domain is not verified. Please, add and verify your domain on https://resend.com/domains","name":"validation_error"}'
    success = _resend_response(200)

    with patch("services.notification_service.httpx.post", side_effect=[unverified_error, success]) as mocked_post:
        notif._send_email("user@example.com", "Subject", "Body")

    assert mocked_post.call_count == 2
    first_sender = mocked_post.call_args_list[0][1]["json"]["from"]
    second_sender = mocked_post.call_args_list[1][1]["json"]["from"]
    assert first_sender == "FarmWise AI <noreply@farmwiseai.app>"
    assert second_sender == "FarmWise AI <onboarding@resend.dev>"


def test_fallback_retry_itself_failing_does_not_loop_forever(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "key")
    monkeypatch.setenv("EMAIL_FROM", "FarmWise AI <noreply@farmwiseai.app>")
    unverified_error = _resend_response(403, {})
    unverified_error.text = '{"statusCode":403,"message":"The farmwiseai.app domain is not verified.","name":"validation_error"}'
    also_fails = _resend_response(500, {})
    also_fails.text = "internal error"

    with patch("services.notification_service.httpx.post", side_effect=[unverified_error, also_fails]) as mocked_post:
        notif._send_email("user@example.com", "Subject", "Body")

    # Exactly 2 attempts — the retry doesn't itself trigger another retry.
    assert mocked_post.call_count == 2


def test_unrelated_403_does_not_trigger_fallback(monkeypatch):
    """A bad API key is also a 403 — retrying with a different 'from'
    address wouldn't fix that, so it must not retry at all."""
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "bad-key")
    monkeypatch.setenv("EMAIL_FROM", "FarmWise AI <noreply@farmwiseai.app>")
    bad_key_error = _resend_response(403, {})
    bad_key_error.text = '{"statusCode":403,"message":"Invalid API key","name":"authentication_error"}'

    with patch("services.notification_service.httpx.post", return_value=bad_key_error) as mocked_post:
        notif._send_email("user@example.com", "Subject", "Body")

    assert mocked_post.call_count == 1


def test_non_403_error_does_not_trigger_fallback(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "key")
    monkeypatch.setenv("EMAIL_FROM", "FarmWise AI <noreply@farmwiseai.app>")
    server_error = _resend_response(500, {})
    server_error.text = "internal server error"

    with patch("services.notification_service.httpx.post", return_value=server_error) as mocked_post:
        notif._send_email("user@example.com", "Subject", "Body")

    assert mocked_post.call_count == 1


def test_no_email_from_set_uses_default_sender_directly(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "key")
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    with patch("services.notification_service.httpx.post", return_value=_resend_response(200)) as mocked_post:
        notif._send_email("user@example.com", "Subject", "Body")
    assert mocked_post.call_args[1]["json"]["from"] == "FarmWise AI <onboarding@resend.dev>"


def test_network_error_logged_not_raised(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "key")
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    import httpx
    with patch("services.notification_service.httpx.post", side_effect=httpx.ConnectError("unreachable")):
        notif._send_email("user@example.com", "Subject", "Body")  # must not raise
