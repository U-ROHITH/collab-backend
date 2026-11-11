"""OTP delivery: 2Factor.in (SMS) and Resend (email, SMTP fallback).

Never log provider URLs (2Factor puts the API key in the path) or codes in live mode.
"""

import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)
_TIMEOUT = 10


class DeliveryError(Exception):
    pass


def send_sms_otp(phone_e164: str, code: str) -> None:
    if _console_mode():
        msg = f"[DEV OTP] SMS to {phone_e164}: {code}"
        logger.warning(msg)
        banner = f"\n========================================\n{msg}\n========================================\n"
        print(banner, flush=True)
        return
    if not settings.TWO_FACTOR_API_KEY:
        logger.error("TWO_FACTOR_API_KEY is not configured. Cannot deliver live SMS.")
        raise DeliveryError
    digits = phone_e164.lstrip("+")
    url = f"https://2factor.in/API/V1/{settings.TWO_FACTOR_API_KEY}/SMS/{digits}/{code}"
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        ok = resp.status_code == 200 and resp.json().get("Status") == "Success"
    except (requests.RequestException, ValueError) as exc:
        logger.error("SMS OTP delivery failed: %s", type(exc).__name__)
        raise DeliveryError from None
    if not ok:
        err_detail = resp.text[:200] if resp else ""
        logger.error("SMS OTP provider rejected request (status %s: %s)", resp.status_code, err_detail)
        raise DeliveryError


def send_email_otp(email: str, code: str) -> None:
    if _console_mode():
        msg = f"[DEV OTP] Email to {email}: {code}"
        logger.warning(msg)
        banner = f"\n========================================\n{msg}\n========================================\n"
        print(banner, flush=True)
        return
    subject = "Your Collab login code"
    text = f"Your login code is {code}. It expires in 5 minutes. If you didn't request it, ignore this email."
    if settings.RESEND_API_KEY:
        try:
            resp = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={"from": settings.RESEND_FROM_EMAIL, "to": [email], "subject": subject, "text": text},
                timeout=_TIMEOUT,
            )
            if resp.status_code < 300:
                return
            logger.error("Resend rejected email (status %s); falling back to SMTP", resp.status_code)
        except requests.RequestException as exc:
            logger.error("Resend failed (%s); falling back to SMTP", type(exc).__name__)
    if not settings.EMAIL_HOST:
        raise DeliveryError
    try:
        send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("SMTP delivery failed: %s", type(exc).__name__)
        raise DeliveryError from None


def _console_mode() -> bool:
    # Console delivery is active when requested via OTP_DELIVERY=console,
    # or as a development fallback when DEBUG=True and no TWO_FACTOR_API_KEY is configured.
    if settings.OTP_DELIVERY == "console":
        return True
    if settings.DEBUG and not getattr(settings, "TWO_FACTOR_API_KEY", ""):
        return True
    return False
