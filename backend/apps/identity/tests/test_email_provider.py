"""Email OTP delivery: a Resend rejection must say why in the logs (e.g. sandbox sender), not just a status."""

from typing import Any

import pytest
import requests

from apps.identity import providers


class _Resp:
    status_code = 403

    def json(self) -> dict[str, Any]:
        return {
            "name": "validation_error",
            "message": "You can only send testing emails to your own email address (owner@example.com).",
        }


@pytest.fixture
def live_resend(settings: Any) -> None:
    settings.OTP_DELIVERY = "live"
    settings.RESEND_API_KEY = "re_test"
    settings.RESEND_FROM_EMAIL = "onboarding@resend.dev"
    settings.EMAIL_HOST = ""


def test_resend_rejection_logs_reason_and_raises(
    live_resend: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    with pytest.raises(providers.DeliveryError):
        providers.send_email_otp("someone@gmail.com", "123456")
    text = caplog.text
    assert "403" in text and "validation_error" in text and "only send testing emails" in text
    assert "123456" not in text and "someone@gmail.com" not in text and "owner@example.com" not in text
