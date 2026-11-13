from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from apps.identity.providers import DeliveryError, send_email_otp


@pytest.mark.django_db
def test_resend_http_client_success():
    with override_settings(
        OTP_DELIVERY="live",
        RESEND_API_KEY="re_test_12345",
        RESEND_FROM_EMAIL="onboarding@resend.dev",
    ):
        mock_resp = MagicMock(status_code=200)
        with patch("requests.post", return_value=mock_resp) as mock_post:
            send_email_otp("user@example.com", "123456")
            assert mock_post.called
            args, kwargs = mock_post.call_args
            assert args[0] == "https://api.resend.com/emails"
            assert kwargs["headers"]["Authorization"] == "Bearer re_test_12345"
            assert kwargs["json"]["from"] == "onboarding@resend.dev"
            assert kwargs["json"]["to"] == ["user@example.com"]
            assert "123456" in kwargs["json"]["text"]


@pytest.mark.django_db
def test_resend_fallback_from_address_to_default_from_email():
    with override_settings(
        OTP_DELIVERY="live",
        RESEND_API_KEY="re_test_12345",
        RESEND_FROM_EMAIL="",
        DEFAULT_FROM_EMAIL="support@linkloom.app",
    ):
        mock_resp = MagicMock(status_code=200)
        with patch("requests.post", return_value=mock_resp) as mock_post:
            send_email_otp("user@example.com", "654321")
            args, kwargs = mock_post.call_args
            assert kwargs["json"]["from"] == "support@linkloom.app"


@pytest.mark.django_db
def test_resend_failure_skips_smtp_on_cloud():
    with override_settings(
        OTP_DELIVERY="live",
        RESEND_API_KEY="re_test_12345",
        RESEND_FROM_EMAIL="onboarding@resend.dev",
        EMAIL_HOST="smtp.gmail.com",
    ):
        mock_resp = MagicMock(status_code=403, text='{"message": "domain not verified"}')
        with patch("requests.post", return_value=mock_resp):
            with patch("django.core.mail.send_mail") as mock_smtp:
                with pytest.raises(DeliveryError):
                    send_email_otp("user@example.com", "123456")
                # Must NOT attempt SMTP when HTTP provider is configured
                assert not mock_smtp.called


@pytest.mark.django_db
def test_brevo_http_client_success():
    with override_settings(
        OTP_DELIVERY="live",
        RESEND_API_KEY="",
        BREVO_API_KEY="xkeysib-test-12345",
        BREVO_FROM_EMAIL="verify@linkloom.app",
    ):
        mock_resp = MagicMock(status_code=201)
        with patch("requests.post", return_value=mock_resp) as mock_post:
            send_email_otp("user@example.com", "789012")
            assert mock_post.called
            args, kwargs = mock_post.call_args
            assert args[0] == "https://api.brevo.com/v3/smtp/email"
            assert kwargs["headers"]["api-key"] == "xkeysib-test-12345"
            assert kwargs["json"]["sender"]["email"] == "verify@linkloom.app"
            assert kwargs["json"]["to"] == [{"email": "user@example.com"}]

