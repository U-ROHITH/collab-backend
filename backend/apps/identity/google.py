"""Google ID-token verification for "Sign in with Google".

The frontend gets an ID token from Google Identity Services and posts it to us. We check the
signature against Google's published certs, the audience (our OAuth client IDs), the issuer and
expiry, using google-auth's JWT decoder. Certs are cached for as long as Google's Cache-Control allows.
"""

import logging
import re
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache
from google.auth import exceptions as google_exc
from google.auth import jwt as google_jwt

logger = logging.getLogger(__name__)

CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
_CACHE_KEY = "google:oauth2:certs"
_STALE_KEY = "google:oauth2:certs:stale"
_STALE_TTL = 24 * 3600
_DEFAULT_CERT_TTL = 3600
_TIMEOUT = 5
_CLOCK_SKEW_SECONDS = 10


class GoogleTokenError(Exception):
    """The token is malformed, expired, for another app, or Google couldn't be reached."""


def _fetch_certs() -> dict[str, str]:
    certs = cache.get(_CACHE_KEY)
    if certs:
        return certs
    try:
        resp = requests.get(CERTS_URL, timeout=_TIMEOUT)
        resp.raise_for_status()
        certs = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Fetching Google certs failed: %s", type(exc).__name__)
        stale = cache.get(_STALE_KEY)
        if stale:  # Google rotates keys slowly; a day-old set keeps logins working through an outage
            cache.set(_CACHE_KEY, stale, 60)
            return stale
        raise GoogleTokenError("Could not reach Google.") from None
    match = re.search(r"max-age=(\d+)", resp.headers.get("Cache-Control", ""))
    cache.set(_CACHE_KEY, certs, int(match.group(1)) if match else _DEFAULT_CERT_TTL)
    cache.set(_STALE_KEY, certs, _STALE_TTL)
    return certs


def verify_id_token(token: str) -> dict[str, Any]:
    """Return the token's claims, or raise GoogleTokenError."""
    try:
        claims = google_jwt.decode(
            token,
            certs=_fetch_certs(),
            audience=list(settings.GOOGLE_OAUTH_CLIENT_IDS),
            clock_skew_in_seconds=_CLOCK_SKEW_SECONDS,
        )
    except (ValueError, google_exc.GoogleAuthError):
        raise GoogleTokenError("Invalid Google token.") from None
    if claims.get("iss") not in _ISSUERS:
        raise GoogleTokenError("Invalid Google token issuer.")
    return claims
