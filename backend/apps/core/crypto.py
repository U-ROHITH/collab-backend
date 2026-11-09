"""App-level field encryption (AES-256-GCM) for the few values we must keep in full (PAN, bank account).

Ciphertext format: "v<version>:<urlsafe base64 of 12-byte nonce + ciphertext+tag>". The key is
SHA-256(DATA_ENCRYPTION_KEY), so any high-entropy string (32+ chars) works as the env value.
Only one key is supported today: changing DATA_ENCRYPTION_KEY or its version makes stored values
unreadable. Rotation (keep old keys for decrypt, re-encrypt, then drop) must be built before rotating.
"""

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class DecryptionError(Exception):
    pass


def _key() -> bytes:
    secret = settings.DATA_ENCRYPTION_KEY
    if not secret:
        raise ImproperlyConfigured("DATA_ENCRYPTION_KEY is not set.")
    return hashlib.sha256(secret.encode()).digest()


def encrypt(plaintext: str, *, context: str) -> str:
    """`context` (e.g. "kyc.pan:<user id>") is bound as associated data, so a ciphertext copied
    into another row or field won't decrypt."""
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, plaintext.encode(), context.encode())
    return f"v{settings.DATA_ENCRYPTION_KEY_VERSION}:" + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(token: str, *, context: str) -> str:
    version, _, body = token.partition(":")
    if version != f"v{settings.DATA_ENCRYPTION_KEY_VERSION}" or not body:
        raise DecryptionError("Unknown key version.")
    raw = base64.urlsafe_b64decode(body.encode())
    try:
        return AESGCM(_key()).decrypt(raw[:12], raw[12:], context.encode()).decode()
    except InvalidTag:
        raise DecryptionError("Ciphertext is invalid.") from None
