"""OTP delivery: 2Factor.in (SMS) and Resend (email, SMTP fallback).

Never log provider URLs (2Factor puts the API key in the path) or codes in live mode.
"""

import logging
import re

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)
_TIMEOUT = 10


class DeliveryError(Exception):
    pass


def send_sms_otp(phone_e164: str, code: str) -> None:
    if _console_mode():
        logger.warning("[DEV OTP] sms to %s: %s", phone_e164, code)
        return
    digits = phone_e164.lstrip("+")
    url = f"https://2factor.in/API/V1/{settings.TWO_FACTOR_API_KEY}/SMS/{digits}/{code}"
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        ok = resp.status_code == 200 and resp.json().get("Status") == "Success"
    except (requests.RequestException, ValueError) as exc:
        logger.error("SMS OTP delivery failed: %s", type(exc).__name__)
        raise DeliveryError from None
    if not ok:
        logger.error("SMS OTP provider rejected request (status %s)", resp.status_code)
        raise DeliveryError


def send_email_otp(email: str, code: str) -> None:
    if _console_mode():
        logger.warning("[DEV OTP] email to %s: %s", email, code)
        return
    subject = "Your Collab login code"
    text = f"Your login code is {code}. It expires in 5 minutes. If you didn't request it, ignore this email."
    if settings.RESEND_API_KEY:
        from_email = settings.RESEND_FROM_EMAIL or settings.DEFAULT_FROM_EMAIL
        try:
            resp = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
                json={"from": from_email, "to": [email], "subject": subject, "text": text},
                timeout=_TIMEOUT,
            )
            if resp.status_code < 300:
                return
            logger.error(
                "Resend rejected email (status %s, %s)", resp.status_code, _resend_reason(resp)
            )
            raise DeliveryError
        except requests.RequestException as exc:
            logger.error("Resend failed (%s)", type(exc).__name__)
            raise DeliveryError from None
    if getattr(settings, "BREVO_API_KEY", None):
        from_email = getattr(settings, "BREVO_FROM_EMAIL", None) or settings.DEFAULT_FROM_EMAIL
        try:
            resp = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": settings.BREVO_API_KEY, "Content-Type": "application/json"},
                json={
                    "sender": {"email": from_email},
                    "to": [{"email": email}],
                    "subject": subject,
                    "textContent": text,
                },
                timeout=_TIMEOUT,
            )
            if resp.status_code < 300:
                return
            logger.error("Brevo rejected email (status %s)", resp.status_code)
            raise DeliveryError
        except requests.RequestException as exc:
            logger.error("Brevo failed (%s)", type(exc).__name__)
            raise DeliveryError from None
    if not settings.EMAIL_HOST:
        raise DeliveryError
    try:
        send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("SMTP delivery failed: %s", type(exc).__name__)
        raise DeliveryError from None


_EMAIL_RE = re.compile(r"[^\s()<>@,;:]+@[^\s()<>@,;:]+")


def _resend_reason(resp: requests.Response) -> str:
    """Resend's error name + message (e.g. the sandbox-sender restriction). Never includes the recipient or code."""
    try:
        body = resp.json()
        message = _EMAIL_RE.sub("<email>", str(body.get("message", "")))[:200]
        return f"{body.get('name', '?')}: {message}"
    except ValueError:
        return "unparseable body"


def _console_mode() -> bool:
    # Console delivery is a dev convenience and is refused unless DEBUG (or tests) — see settings.
    return settings.OTP_DELIVERY == "console"
