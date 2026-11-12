"""Password sign-in, password at signup, and password reset (set a new one after signing in with a code)."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.identity import providers
from apps.identity.models import AuditLog, User, UserStatus
from apps.identity.services import issue_tokens

pytestmark = pytest.mark.django_db

GOOD = "Monsoon-Tea-42"


def _auth(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(user).access}")
    return client


def _login(client: APIClient, identifier: str, password: str) -> Any:
    return client.post("/v1/auth/login/", {"identifier": identifier, "password": password}, format="json")


def _signup(client: APIClient, **extra: Any) -> Any:
    body = {"role": "creator", "fullName": "Ananya Rao", **extra}
    return client.post("/v1/auth/signup/complete/", body, format="json")


# --- POST /v1/auth/login/ ------------------------------------------------------------------------


def test_login_with_email_returns_tokens(anon: APIClient) -> None:
    user = User.objects.create_user(email="ananya@example.com", password=GOOD)
    resp = _login(anon, "Ananya@Example.com ", GOOD)
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["access"] and data["refresh"]
    assert data["user"]["id"] == str(user.id)
    assert AuditLog.objects.filter(action="user.login_password", actor=user).exists()


@pytest.mark.parametrize("identifier", ["9876543210", "+91 98765 43210", "098765-43210"])
def test_login_with_phone_in_any_common_format(anon: APIClient, identifier: str) -> None:
    User.objects.create_user(phone="+919876543210", password=GOOD)
    resp = _login(anon, identifier, GOOD)
    assert resp.status_code == 200, resp.json()


def test_wrong_password_and_unknown_account_look_identical(anon: APIClient) -> None:
    User.objects.create_user(email="ananya@example.com", password=GOOD)
    wrong = _login(anon, "ananya@example.com", "not-the-password")
    unknown = _login(anon, "nobody@example.com", GOOD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "AUTH_INVALID"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_account_without_password_cannot_password_login(anon: APIClient) -> None:
    User.objects.create_user(email="otp-only@example.com")  # signed up with a code, never set a password
    resp = _login(anon, "otp-only@example.com", "")
    assert resp.status_code == 400  # blank password fails validation
    resp = _login(anon, "otp-only@example.com", "anything-at-all")
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "AUTH_INVALID"


@pytest.mark.parametrize("status_value", [UserStatus.SUSPENDED, UserStatus.DEACTIVATED])
def test_suspended_or_deactivated_account_cannot_log_in(anon: APIClient, status_value: str) -> None:
    User.objects.create_user(email="ananya@example.com", password=GOOD, status=status_value)
    resp = _login(anon, "ananya@example.com", GOOD)
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "AUTH_INVALID"


def test_login_rejects_unknown_fields(anon: APIClient) -> None:
    resp = anon.post(
        "/v1/auth/login/", {"identifier": "a@example.com", "password": GOOD, "role": "admin"}, format="json"
    )
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_repeated_wrong_passwords_lock_the_account_for_a_while(anon: APIClient) -> None:
    User.objects.create_user(email="ananya@example.com", password=GOOD)
    codes = [_login(anon, "ananya@example.com", "wrong-guess").status_code for _ in range(10)]
    assert set(codes) == {401}
    locked = _login(anon, "ANANYA@example.com", GOOD)  # even the right password, any casing
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "RATE_LIMITED"


def test_successful_login_resets_the_failure_count(anon: APIClient) -> None:
    User.objects.create_user(email="ananya@example.com", password=GOOD)
    for _ in range(9):
        _login(anon, "ananya@example.com", "wrong-guess")
    assert _login(anon, "ananya@example.com", GOOD).status_code == 200
    for _ in range(9):
        _login(anon, "ananya@example.com", "wrong-guess")
    assert _login(anon, "ananya@example.com", GOOD).status_code == 200


# --- password at /signup/complete/ --------------------------------------------------------------


def test_signup_with_password_then_password_login_works(anon: APIClient) -> None:
    user = User.objects.create_user(email="ananya@example.com")
    resp = _signup(_auth(user), password=GOOD, confirmPassword=GOOD)
    assert resp.status_code == 201, resp.json()
    assert _login(anon, "ananya@example.com", GOOD).status_code == 200


def test_signup_without_password_still_works(anon: APIClient) -> None:
    user = User.objects.create_user(email="ananya@example.com")
    assert _signup(_auth(user)).status_code == 201
    user.refresh_from_db()
    assert not user.has_usable_password()


def test_signup_password_mismatch_is_reported_on_confirm_field() -> None:
    user = User.objects.create_user(email="ananya@example.com")
    resp = _signup(_auth(user), password=GOOD, confirmPassword=GOOD + "x")
    assert resp.status_code == 400
    assert "confirmPassword" in resp.json()["error"]["details"]
    user.refresh_from_db()
    assert user.role is None  # nothing saved


@pytest.mark.parametrize("bad", ["short1", "12345678901", "password123"])
def test_signup_weak_password_is_reported_on_password_field(bad: str) -> None:
    user = User.objects.create_user(email="ananya@example.com")
    resp = _signup(_auth(user), password=bad, confirmPassword=bad)
    assert resp.status_code == 400
    assert "password" in resp.json()["error"]["details"]


def test_signup_password_requires_confirmation() -> None:
    user = User.objects.create_user(email="ananya@example.com")
    resp = _signup(_auth(user), password=GOOD)
    assert resp.status_code == 400
    assert "confirmPassword" in resp.json()["error"]["details"]


# --- POST /v1/auth/password/reset/ (forgot password, needs a fresh code) ------------------------


@pytest.fixture
def sent_codes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    codes: list[str] = []
    monkeypatch.setattr(providers, "send_sms_otp", lambda _dest, code: codes.append(code))
    monkeypatch.setattr(providers, "send_email_otp", lambda _dest, code: codes.append(code))
    return codes


def _reset(client: APIClient, sent_codes: list[str], email: str, password: str, confirm: str | None = None) -> Any:
    otp = client.post("/v1/auth/otp/request/", {"channel": "email", "destination": email}, format="json")
    body = {
        "challengeId": otp.json()["data"]["challengeId"],
        "code": sent_codes[-1],
        "password": password,
        "confirmPassword": password if confirm is None else confirm,
    }
    return client.post("/v1/auth/password/reset/", body, format="json")


def test_reset_password_with_code(anon: APIClient, sent_codes: list[str]) -> None:
    user = User.objects.create_user(email="ananya@example.com", password="Old-Password-99")
    resp = _reset(anon, sent_codes, "ananya@example.com", GOOD)
    assert resp.status_code == 200, resp.content
    assert resp.json()["data"]["access"] and resp.json()["data"]["user"]["id"] == str(user.id)
    assert _login(anon, "ananya@example.com", GOOD).status_code == 200
    assert _login(anon, "ananya@example.com", "Old-Password-99").status_code == 401
    assert AuditLog.objects.filter(action="user.password_reset", actor=user).exists()


def test_reset_lets_a_code_only_account_add_a_password(anon: APIClient, sent_codes: list[str]) -> None:
    User.objects.create_user(email="ananya@example.com")
    assert _reset(anon, sent_codes, "ananya@example.com", GOOD).status_code == 200
    assert _login(anon, "ananya@example.com", GOOD).status_code == 200


def test_reset_signs_out_other_sessions(anon: APIClient, sent_codes: list[str]) -> None:
    user = User.objects.create_user(email="ananya@example.com")
    other_refresh = issue_tokens(user).refresh
    assert _reset(anon, sent_codes, "ananya@example.com", GOOD).status_code == 200
    resp = APIClient().post("/v1/auth/token/refresh/", {"refresh": other_refresh}, format="json")
    assert resp.status_code == 401


def test_reset_clears_a_password_lockout(anon: APIClient, sent_codes: list[str]) -> None:
    User.objects.create_user(email="ananya@example.com", password="Old-Password-99")
    for _ in range(10):
        _login(anon, "ananya@example.com", "wrong-guess")
    assert _reset(anon, sent_codes, "ananya@example.com", GOOD).status_code == 200
    assert _login(anon, "ananya@example.com", GOOD).status_code == 200


def test_reset_with_wrong_code_changes_nothing(anon: APIClient, sent_codes: list[str]) -> None:
    User.objects.create_user(email="ananya@example.com", password="Old-Password-99")
    otp = anon.post("/v1/auth/otp/request/", {"channel": "email", "destination": "ananya@example.com"}, format="json")
    wrong = "000000" if sent_codes[-1] != "000000" else "111111"
    body = {"challengeId": otp.json()["data"]["challengeId"], "code": wrong, "password": GOOD, "confirmPassword": GOOD}
    resp = anon.post("/v1/auth/password/reset/", body, format="json")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "OTP_INVALID"
    assert _login(anon, "ananya@example.com", "Old-Password-99").status_code == 200


def test_reset_validates_password_before_using_the_code(anon: APIClient, sent_codes: list[str]) -> None:
    User.objects.create_user(email="ananya@example.com")
    resp = _reset(anon, sent_codes, "ananya@example.com", "short")
    assert resp.status_code == 400 and "password" in resp.json()["error"]["details"]
    resp = _reset(anon, sent_codes, "ananya@example.com", GOOD, confirm="different-one")
    assert resp.status_code == 400 and "confirmPassword" in resp.json()["error"]["details"]


def test_signed_in_token_alone_cannot_set_a_password() -> None:
    user = User.objects.create_user(email="ananya@example.com")
    resp = _auth(user).post("/v1/auth/password/", {"password": GOOD, "confirmPassword": GOOD}, format="json")
    assert resp.status_code == 404
