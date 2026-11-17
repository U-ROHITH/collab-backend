"""OTP delivery: 2Factor.in (SMS) and Resend (email, SMTP fallback).

Never log provider URLs (2Factor puts the API key in the path) or codes in live mode.
"""

import logging
import re
from email.utils import parseaddr

import requests
from django.conf import settings
from django.core import mail

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
    """HTTP providers first (Resend, then Brevo). SMTP only when no HTTP provider is configured: Render blocks
    outbound SMTP, so trying it after an HTTP failure would just add a 10s timeout before the same error."""
    if _console_mode():
        logger.warning("[DEV OTP] email to %s: %s", email, code)
        return
    subject = "Your Collab login code"
    text = f"Your login code is {code}. It expires in 5 minutes. If you didn't request it, ignore this email."
    http_providers = [p for p in (_send_resend, _send_brevo) if p.enabled()]  # type: ignore[attr-defined]
    for send in http_providers:
        if send(email, subject, text):
            return
    if http_providers or not settings.EMAIL_HOST:
        raise DeliveryError
    try:
        mail.send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        logger.error("SMTP delivery failed: %s", type(exc).__name__)
        raise DeliveryError from None


def _send_resend(email: str, subject: str, text: str) -> bool:
    sender = settings.RESEND_FROM_EMAIL or settings.DEFAULT_FROM_EMAIL
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            json={"from": sender, "to": [email], "subject": subject, "text": text},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("Resend failed (%s)", type(exc).__name__)
        return False
    if resp.status_code < 300:
        return True
    logger.error("Resend rejected email (status %s, %s)", resp.status_code, _resend_reason(resp))
    return False


_send_resend.enabled = lambda: bool(settings.RESEND_API_KEY)  # type: ignore[attr-defined]


def _send_brevo(email: str, subject: str, text: str) -> bool:
    name, address = parseaddr(getattr(settings, "BREVO_FROM_EMAIL", "") or settings.DEFAULT_FROM_EMAIL)
    sender = {"email": address, **({"name": name} if name else {})}
    try:
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": settings.BREVO_API_KEY, "accept": "application/json"},
            json={"sender": sender, "to": [{"email": email}], "subject": subject, "textContent": text},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("Brevo failed (%s)", type(exc).__name__)
        return False
    if resp.status_code < 300:
        return True
    logger.error("Brevo rejected email (status %s, %s)", resp.status_code, _resend_reason(resp))
    return False


_send_brevo.enabled = lambda: bool(getattr(settings, "BREVO_API_KEY", ""))  # type: ignore[attr-defined]


_EMAIL_RE = re.compile(r"[^\s()<>@,;:]+@[^\s()<>@,;:]+")


def _resend_reason(resp: requests.Response) -> str:
    """Resend's error name + message (e.g. the sandbox-sender restriction). Never includes the recipient or code."""
    try:
        body = resp.json()
        message = _EMAIL_RE.sub("<email>", str(body.get("message", "")))[:200]
        return f"{body.get('name') or body.get('code') or '?'}: {message}"
    except ValueError:
        return "unparseable body"


def _console_mode() -> bool:
    # Console delivery is a dev convenience and is refused unless DEBUG (or tests) — see settings.
    return settings.OTP_DELIVERY == "console"
