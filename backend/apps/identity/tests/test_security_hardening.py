"""Security review fixes (2026-11-12): purpose-bound codes, per-IP lockout, token invalidation, reset checks."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.identity import providers
from apps.identity.models import User
from apps.identity.services import issue_tokens

pytestmark = pytest.mark.django_db

GOOD = "Monsoon-Tea-42"
EMAIL = "ananya.rao@example.com"


@pytest.fixture
def sent_codes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    codes: list[str] = []
    monkeypatch.setattr(providers, "send_sms_otp", lambda _dest, code: codes.append(code))
    monkeypatch.setattr(providers, "send_email_otp", lambda _dest, code: codes.append(code))
    return codes


def _code(client: APIClient, sent: list[str], purpose: str | None = None, dest: str = EMAIL) -> tuple[str, str]:
    body: dict[str, Any] = {"channel": "email", "destination": dest}
    if purpose:
        body["purpose"] = purpose
    resp = client.post("/v1/auth/otp/request/", body, format="json")
    assert resp.status_code == 202, resp.content
    return resp.json()["data"]["challengeId"], sent[-1]


def _reset(client: APIClient, challenge: str, code: str, password: str = GOOD) -> Any:
    body = {"challengeId": challenge, "code": code, "password": password, "confirmPassword": password}
    return client.post("/v1/auth/password/reset/", body, format="json")


def _login(client: APIClient, password: str = GOOD, ip: str = "203.0.113.1") -> Any:
    return client.post("/v1/auth/login/", {"identifier": EMAIL, "password": password}, format="json", REMOTE_ADDR=ip)


def _bearer(access: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


# --- Codes are bound to their purpose ------------------------------------------------------------


def test_sign_in_code_cannot_reset_a_password(anon: APIClient, sent_codes: list[str]) -> None:
    User.objects.create_user(email=EMAIL, password="Old-Password-99")
    challenge, code = _code(anon, sent_codes)  # default purpose: login
    resp = _reset(anon, challenge, code)
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "OTP_INVALID"
    assert _login(anon, "Old-Password-99").status_code == 200


def test_reset_code_resets_but_cannot_sign_in(anon: APIClient, sent_codes: list[str]) -> None:
    User.objects.create_user(email=EMAIL, password="Old-Password-99")
    challenge, code = _code(anon, sent_codes, "password_reset")
    resp = anon.post("/v1/auth/otp/verify/", {"challengeId": challenge, "code": code}, format="json")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "OTP_INVALID"
    challenge, code = _code(anon, sent_codes, "password_reset")
    assert _reset(anon, challenge, code).status_code == 200


def test_unknown_purpose_is_rejected(anon: APIClient) -> None:
    resp = anon.post(
        "/v1/auth/otp/request/", {"channel": "email", "destination": EMAIL, "purpose": "admin"}, format="json"
    )
    assert resp.status_code == 400


# --- A code sign-in alone can't replace an existing password -------------------------------------


def test_fresh_code_session_cannot_replace_an_existing_password() -> None:
    user = User.objects.create_user(email=EMAIL, password="Old-Password-99")
    client = _bearer(issue_tokens(user, method="otp").access)
    resp = client.post("/v1/auth/password/change/", {"password": GOOD, "confirmPassword": GOOD}, format="json")
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "REAUTH_REQUIRED"


def test_fresh_code_session_can_set_a_first_password() -> None:
    user = User.objects.create_user(email=EMAIL)
    client = _bearer(issue_tokens(user, method="otp").access)
    resp = client.post("/v1/auth/password/change/", {"password": GOOD, "confirmPassword": GOOD}, format="json")
    assert resp.status_code == 200


# --- Lockout is per account and IP, with a per-account ceiling -----------------------------------


def test_someone_else_cannot_lock_the_owner_out(anon: APIClient) -> None:
    User.objects.create_user(email=EMAIL, password=GOOD)
    for _ in range(11):
        _login(anon, "wrong-guess", ip="198.51.100.66")  # attacker
    assert _login(anon, GOOD, ip="198.51.100.66").status_code == 429
    assert _login(anon, GOOD, ip="203.0.113.1").status_code == 200  # owner, elsewhere


def test_spread_out_guessing_still_hits_an_account_ceiling(anon: APIClient) -> None:
    User.objects.create_user(email=EMAIL, password=GOOD)
    for i in range(50):
        _login(anon, "wrong-guess", ip=f"198.51.100.{i}")
    assert _login(anon, GOOD, ip="203.0.113.1").status_code == 429


def test_reset_clears_the_lockout_for_the_resetting_ip(sent_codes: list[str]) -> None:
    anon = APIClient(REMOTE_ADDR="203.0.113.1")  # the owner resets from the device that got locked out
    User.objects.create_user(email=EMAIL, password="Old-Password-99")
    for _ in range(11):
        _login(anon, "wrong-guess")
    challenge, code = _code(anon, sent_codes, "password_reset")
    assert _reset(anon, challenge, code).status_code == 200
    assert _login(anon, GOOD).status_code == 200


# --- Tokens issued before a password change stop working at once --------------------------------


def test_old_access_token_is_rejected_after_password_change(anon: APIClient, sent_codes: list[str]) -> None:
    user = User.objects.create_user(email=EMAIL, password="Old-Password-99")
    old = _bearer(issue_tokens(user, method="password").access)
    assert old.get("/v1/auth/me/").status_code == 200
    challenge, code = _code(anon, sent_codes, "password_reset")
    new_access = _reset(anon, challenge, code).json()["data"]["access"]
    assert old.get("/v1/auth/me/").status_code == 401
    assert _bearer(new_access).get("/v1/auth/me/").status_code == 200


def test_setting_a_password_at_signup_keeps_the_current_session() -> None:
    user = User.objects.create_user(email=EMAIL)
    client = _bearer(issue_tokens(user, method="otp").access)
    body = {"role": "creator", "fullName": "Ananya Rao", "password": GOOD, "confirmPassword": GOOD}
    assert client.post("/v1/auth/signup/complete/", body, format="json").status_code == 201
    assert client.get("/v1/auth/me/").status_code == 200


# --- Reset rejects passwords that resemble the account -------------------------------------------


def test_reset_rejects_a_password_like_the_email(anon: APIClient, sent_codes: list[str]) -> None:
    User.objects.create_user(email=EMAIL, password="Old-Password-99")
    challenge, code = _code(anon, sent_codes, "password_reset")
    resp = _reset(anon, challenge, code, password="ananya.rao")
    assert resp.status_code == 400 and "password" in resp.json()["error"]["details"]


def test_sessions_from_before_this_release_keep_working_for_code_only_accounts() -> None:
    from rest_framework_simplejwt.tokens import RefreshToken

    user = User.objects.create_user(email=EMAIL)  # no password
    refresh = RefreshToken.for_user(user)
    refresh["amr"], refresh["pwv"] = "otp", "0123456789ab"  # old-format fingerprint
    assert _bearer(str(refresh.access_token)).get("/v1/auth/me/").status_code == 200
