import datetime as dt
import time
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.conf import settings
from google.auth import crypt
from google.auth import jwt as google_jwt
from rest_framework.test import APIClient

from apps.identity import google
from apps.identity.models import Role, User, UserStatus

pytestmark = pytest.mark.django_db

CLIENT_ID = "1042168589486-k7e30v1tao2ooepsb5lp339m7anlvq44.apps.googleusercontent.com"
URL = "/v1/auth/google/"


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    """A throwaway RSA key + self-signed cert standing in for Google's signing key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return pem_key, cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture
def google_certs(monkeypatch: pytest.MonkeyPatch, keypair: tuple[str, str]) -> None:
    monkeypatch.setattr(google, "_fetch_certs", lambda: {"kid1": keypair[1]})


def _token(keypair: tuple[str, str], **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "Asha@Example.com",
        "email_verified": True,
        "name": "Asha Verma",
        "iat": now,
        "exp": now + 600,
        **overrides,
    }
    signer = crypt.RSASigner.from_string(keypair[0], key_id="kid1")
    return google_jwt.encode(signer, claims).decode()


def test_client_id_configured() -> None:
    assert CLIENT_ID in settings.GOOGLE_OAUTH_CLIENT_IDS


def test_new_user_signs_up_with_role(anon: APIClient, google_certs: None, keypair: tuple[str, str]) -> None:
    resp = anon.post(URL, {"idToken": _token(keypair), "role": "creator"}, format="json")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["access"] and data["refresh"]
    assert data["user"]["email"] == "asha@example.com"
    # Even if role is passed in request, new users require onboarding before role is assigned
    assert data["user"]["role"] is None and data["user"]["needsOnboarding"] is True
    assert data["user"]["fullName"] == "Asha Verma"
    assert data["user"]["kycStatus"] == "not_started"


def test_without_role_needs_onboarding(anon: APIClient, google_certs: None, keypair: tuple[str, str]) -> None:
    data = anon.post(URL, {"idToken": _token(keypair)}, format="json").json()["data"]
    assert data["user"]["role"] is None and data["user"]["needsOnboarding"] is True


def test_existing_account_keeps_its_role(
    anon: APIClient, google_certs: None, keypair: tuple[str, str], make_brand: Any
) -> None:
    brand = make_brand()
    token = _token(keypair, email=brand.user.email)
    data = anon.post(URL, {"idToken": token, "role": "creator"}, format="json").json()["data"]
    assert data["user"]["id"] == str(brand.user.pk)
    assert data["user"]["role"] == Role.BRAND


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"aud": "someone-elses-client.apps.googleusercontent.com"}, "GOOGLE_TOKEN_INVALID"),
        ({"iss": "https://evil.example.com"}, "GOOGLE_TOKEN_INVALID"),
        ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}, "GOOGLE_TOKEN_INVALID"),
        ({"email_verified": False}, "GOOGLE_EMAIL_UNVERIFIED"),
        ({"email": ""}, "GOOGLE_EMAIL_UNVERIFIED"),
    ],
)
def test_bad_tokens_rejected(
    anon: APIClient, google_certs: None, keypair: tuple[str, str], overrides: dict[str, Any], code: str
) -> None:
    resp = anon.post(URL, {"idToken": _token(keypair, **overrides)}, format="json")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == code
    assert not User.objects.exists()


def test_forged_signature_rejected(anon: APIClient, google_certs: None, keypair: tuple[str, str]) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = other_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    forged = _token((pem, keypair[1]))
    resp = anon.post(URL, {"idToken": forged}, format="json")
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "GOOGLE_TOKEN_INVALID"


def test_garbage_and_admin_role_rejected(anon: APIClient, google_certs: None, keypair: tuple[str, str]) -> None:
    assert anon.post(URL, {"idToken": "not-a-jwt"}, format="json").status_code == 401
    resp = anon.post(URL, {"idToken": _token(keypair), "role": "admin"}, format="json")
    assert resp.status_code == 400 and "role" in resp.json()["error"]["details"]


def test_suspended_user_rejected(anon: APIClient, google_certs: None, keypair: tuple[str, str]) -> None:
    User.objects.create_user(email="asha@example.com", status=UserStatus.SUSPENDED)
    assert anon.post(URL, {"idToken": _token(keypair)}, format="json").status_code == 401


def test_google_unreachable(anon: APIClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def boom(*_a: Any, **_k: Any) -> None:
        raise requests.ConnectionError

    monkeypatch.setattr(google.requests, "get", boom)
    resp = anon.post(URL, {"idToken": "a.b.c"}, format="json")
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "GOOGLE_TOKEN_INVALID"


def test_certs_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Resp:
        headers = {"Cache-Control": "public, max-age=1200"}

        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, str]:
            return {"k": "cert"}

    monkeypatch.setattr(google.requests, "get", lambda url, timeout: calls.append(url) or Resp())
    assert google._fetch_certs() == {"k": "cert"}
    assert google._fetch_certs() == {"k": "cert"}
    assert calls == [google.CERTS_URL]


def test_stale_certs_used_when_google_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests
    from django.core.cache import cache

    cache.set(google._STALE_KEY, {"k": "old-cert"}, 100)

    def boom(*_a: Any, **_k: Any) -> None:
        raise requests.ConnectionError

    monkeypatch.setattr(google.requests, "get", boom)
    assert google._fetch_certs() == {"k": "old-cert"}
