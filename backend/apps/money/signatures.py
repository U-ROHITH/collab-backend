"""Razorpay proof-of-payment checks. Nothing may mark an order funded without one of these passing.

- Checkout callback (from the brand's browser): HMAC-SHA256 of "<razorpay_order_id>|<razorpay_payment_id>"
  with the key secret, as returned by Razorpay Checkout in `razorpay_signature`.
- Webhook (from Razorpay's servers): HMAC-SHA256 of the raw request body with the webhook secret, in the
  `X-Razorpay-Signature` header.

The settings defaults for these secrets are public (they're in the repo), so a signature made with them proves
nothing. Until real secrets are configured, payments are refused.
"""

import hashlib
import hmac

from django.conf import settings

from apps.core.exceptions import DomainError

_PLACEHOLDER_SECRETS = frozenset({"", "test_secret_collab", "test_webhook_secret"})


class PaymentsNotConfigured(DomainError):
    status_code = 503
    code = "PAYMENTS_NOT_CONFIGURED"
    message = "Payments aren't available yet."


class InvalidSignature(DomainError):
    status_code = 400
    code = "INVALID_SIGNATURE"
    message = "Payment could not be verified."


def _secret(name: str) -> str:
    value = getattr(settings, name, "") or ""
    if value in _PLACEHOLDER_SECRETS:
        raise PaymentsNotConfigured
    return value


def _matches(secret: str, message: bytes, signature: str) -> bool:
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def check_checkout_signature(*, razorpay_order_id: str, payment_id: str, signature: str) -> None:
    secret = _secret("RAZORPAY_KEY_SECRET")
    if not _matches(secret, f"{razorpay_order_id}|{payment_id}".encode(), signature):
        raise InvalidSignature


def check_webhook_signature(*, raw_body: bytes, signature: str) -> None:
    secret = _secret("RAZORPAY_WEBHOOK_SECRET")
    if not _matches(secret, raw_body, signature):
        raise InvalidSignature
