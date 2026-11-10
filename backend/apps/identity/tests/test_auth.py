from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity import providers
from apps.identity.models import OtpChallenge, User, UserStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def sent_codes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    codes: list[str] = []
    monkeypatch.setattr(providers, "send_sms_otp", lambda _dest, code: codes.append(code))
    monkeypatch.setattr(providers, "send_email_otp", lambda _dest, code: codes.append(code))
    return codes


def _request(client: APIClient, destination: str = "98765 43210", channel: str = "sms") -> Any:
    return client.post("/v1/auth/otp/request/", {"channel": channel, "destination": destination}, format="json")


def _login(client: APIClient, sent_codes: list[str], destination: str = "9876543210") -> dict[str, Any]:
    challenge = _request(client, destination).json()["data"]["challengeId"]
    resp = client.post("/v1/auth/otp/verify/", {"challengeId": challenge, "code": sent_codes[-1]}, format="json")
    assert resp.status_code == 200, resp.json()
    return resp.json()["data"]


def test_otp_login_creates_user_and_returns_tokens(anon: APIClient, sent_codes: list[str]) -> None:
    data = _login(anon, sent_codes)
    assert data["access"] and data["refresh"]
    assert data["user"]["phone"] == "+919876543210"
    assert data["user"]["needsOnboarding"] is True
    # code stored only as a hash
    assert sent_codes[-1] not in OtpChallenge.objects.get().code_hash


def test_envelope_shape_and_request_id(anon: APIClient, sent_codes: list[str]) -> None:
    resp = _request(anon)
    body = resp.json()
    assert resp.status_code == 202
    assert set(body) == {"success", "data", "error", "meta"} and body["success"] is True
    assert resp["X-Request-ID"]


def test_invalid_phone_rejected(anon: APIClient, sent_codes: list[str]) -> None:
    resp = _request(anon, "12345")
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "INVALID_PHONE"


def test_unknown_fields_rejected(anon: APIClient) -> None:
    resp = anon.post(
        "/v1/auth/otp/request/", {"channel": "sms", "destination": "9876543210", "role": "admin"}, format="json"
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "role" in resp.json()["error"]["details"]


def test_wrong_code_counts_attempts_and_locks(anon: APIClient, sent_codes: list[str]) -> None:
    challenge_id = _request(anon).json()["data"]["challengeId"]
    wrong = "000000" if sent_codes[-1] != "000000" else "111111"
    for _ in range(5):
        r = anon.post("/v1/auth/otp/verify/", {"challengeId": challenge_id, "code": wrong}, format="json")
        assert r.status_code == 400 and r.json()["error"]["code"] == "OTP_INVALID"
    assert OtpChallenge.objects.get(pk=challenge_id).attempts == 5
    # even the right code is refused once locked
    r = anon.post("/v1/auth/otp/verify/", {"challengeId": challenge_id, "code": sent_codes[-1]}, format="json")
    assert r.status_code == 400


def test_code_is_single_use_and_expires(anon: APIClient, sent_codes: list[str]) -> None:
    challenge_id = _request(anon).json()["data"]["challengeId"]
    ok = anon.post("/v1/auth/otp/verify/", {"challengeId": challenge_id, "code": sent_codes[-1]}, format="json")
    assert ok.status_code == 200
    again = anon.post("/v1/auth/otp/verify/", {"challengeId": challenge_id, "code": sent_codes[-1]}, format="json")
    assert again.status_code == 400

    challenge_id = _request(anon).json()["data"]["challengeId"]
    OtpChallenge.objects.filter(pk=challenge_id).update(expires_at=timezone.now() - timedelta(seconds=1))
    expired = anon.post("/v1/auth/otp/verify/", {"challengeId": challenge_id, "code": sent_codes[-1]}, format="json")
    assert expired.status_code == 400


def test_new_code_invalidates_previous(anon: APIClient, sent_codes: list[str]) -> None:
    first = _request(anon).json()["data"]["challengeId"]
    first_code = sent_codes[-1]
    _request(anon)
    r = anon.post("/v1/auth/otp/verify/", {"challengeId": first, "code": first_code}, format="json")
    assert r.status_code == 400


def test_per_destination_hourly_limit(anon: APIClient, sent_codes: list[str]) -> None:
    for _ in range(5):
        assert _request(anon).status_code == 202
    r = _request(anon)
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"


def test_signup_complete_sets_role_once_and_never_admin(anon: APIClient, sent_codes: list[str]) -> None:
    tokens = _login(anon, sent_codes)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    admin_try = client.post("/v1/auth/signup/complete/", {"role": "admin", "fullName": "X"}, format="json")
    assert admin_try.status_code == 400

    ok = client.post("/v1/auth/signup/complete/", {"role": "creator", "fullName": "Ananya"}, format="json")
    assert ok.status_code == 201 and ok.json()["data"]["role"] == "creator"
    assert ok.json()["data"]["profileId"]

    again = client.post("/v1/auth/signup/complete/", {"role": "brand", "fullName": "Ananya"}, format="json")
    assert again.status_code == 409 and again.json()["error"]["code"] == "SIGNUP_ALREADY_COMPLETE"


def test_suspended_user_is_locked_out_immediately(anon: APIClient, sent_codes: list[str]) -> None:
    tokens = _login(anon, sent_codes)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert client.get("/v1/auth/me/").status_code == 200
    User.objects.update(status=UserStatus.SUSPENDED)
    assert client.get("/v1/auth/me/").status_code == 401


def test_refresh_rotation_and_logout(anon: APIClient, sent_codes: list[str]) -> None:
    tokens = _login(anon, sent_codes)
    r1 = anon.post("/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json")
    assert r1.status_code == 200
    new_refresh = r1.json()["data"]["refresh"]
    # old refresh token is blacklisted after rotation
    assert anon.post("/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json").status_code == 401

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {r1.json()['data']['access']}")
    assert client.post("/v1/auth/logout/", {"refresh": new_refresh}, format="json").status_code == 204
    assert anon.post("/v1/auth/token/refresh/", {"refresh": new_refresh}, format="json").status_code == 401


def test_unauthenticated_and_garbage_tokens(anon: APIClient) -> None:
    assert anon.get("/v1/auth/me/").status_code == 401
    anon.credentials(HTTP_AUTHORIZATION="Bearer not.a.jwt")
    r = anon.get("/v1/auth/me/")
    assert r.status_code == 401 and r.json()["success"] is False


def test_email_verification_lifecycle(anon: APIClient, sent_codes: list[str]) -> None:
    tokens = _login(anon, sent_codes)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    # Check me endpoint exposes emailVerified: False
    me = client.get("/v1/auth/me/").json()["data"]
    assert me["emailVerified"] is False

    # Request email verification without auth -> 401
    assert anon.post("/v1/auth/email/request/", {"email": "founder@brand.com"}, format="json").status_code == 401

    # Request email verification as authenticated user
    req = client.post("/v1/auth/email/request/", {"email": "founder@brand.com"}, format="json")
    assert req.status_code == 200, req.json()
    challenge_id = req.json()["data"]["challengeId"]
    assert challenge_id

    # Verify with wrong code -> 400
    bad_verify = client.post("/v1/auth/email/verify/", {"challengeId": challenge_id, "code": "000000"}, format="json")
    assert bad_verify.status_code == 400
    assert bad_verify.json()["error"]["code"] == "OTP_INVALID"

    # Verify with correct code -> 200
    verify = client.post("/v1/auth/email/verify/", {"challengeId": challenge_id, "code": sent_codes[-1]}, format="json")
    assert verify.status_code == 200, verify.json()
    assert verify.json()["data"]["email"] == "founder@brand.com"
    assert verify.json()["data"]["emailVerified"] is True

    # User profile should now reflect verified email
    me_after = client.get("/v1/auth/me/").json()["data"]
    assert me_after["email"] == "founder@brand.com"
    assert me_after["emailVerified"] is True

    # Complete signup and verify values are preserved
    signup = client.post(
        "/v1/auth/signup/complete/",
        {"role": "brand", "fullName": "Founder Name", "brandName": "New Brand"},
        format="json",
    )
    assert signup.status_code == 201
    assert signup.json()["data"]["email"] == "founder@brand.com"
    assert signup.json()["data"]["emailVerified"] is True

    # Token refresh also includes user with email and emailVerified
    refresh_resp = anon.post("/v1/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json")
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["data"]["user"]["email"] == "founder@brand.com"
    assert refresh_resp.json()["data"]["user"]["emailVerified"] is True


def test_email_verification_duplicate_email_rejected(anon: APIClient, sent_codes: list[str]) -> None:
    User.objects.create_user(email="taken@example.com", phone="+919999900001")

    tokens = _login(anon, sent_codes, destination="9876500002")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    r = client.post("/v1/auth/email/request/", {"email": "taken@example.com"}, format="json")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_ALREADY_IN_USE"


def test_signup_complete_with_password_success(anon: APIClient, sent_codes: list[str]) -> None:
    tokens = _login(anon, sent_codes, destination="9876500003")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    payload = {
        "role": "creator",
        "fullName": "Priya Sharma",
        "displayName": "priya_official",
        "password": "StrongPassword123!",
        "confirmPassword": "StrongPassword123!",
    }
    resp = client.post("/v1/auth/signup/complete/", payload, format="json")
    assert resp.status_code == 201, resp.json()
    user = User.objects.get(phone="+919876500003")
    assert user.role == "creator"
    assert user.has_usable_password() is True
    assert user.check_password("StrongPassword123!") is True


def test_signup_complete_with_password_validation_failures(anon: APIClient, sent_codes: list[str]) -> None:
    tokens = _login(anon, sent_codes, destination="9876500004")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    # 1. Passwords do not match
    r1 = client.post(
        "/v1/auth/signup/complete/",
        {
            "role": "creator",
            "fullName": "Priya",
            "password": "StrongPassword123!",
            "confirmPassword": "DifferentPassword456!",
        },
        format="json",
    )
    assert r1.status_code == 400
    assert "confirmPassword" in r1.json()["error"]["details"]
    assert "Passwords do not match" in r1.json()["error"]["details"]["confirmPassword"][0]

    # 2. Password is too short (< 8 chars)
    r2 = client.post(
        "/v1/auth/signup/complete/",
        {
            "role": "creator",
            "fullName": "Priya",
            "password": "short",
            "confirmPassword": "short",
        },
        format="json",
    )
    assert r2.status_code == 400
    assert "password" in r2.json()["error"]["details"]
    assert "8 characters" in r2.json()["error"]["details"]["password"][0]

    # 3. Password provided without confirmPassword
    r3 = client.post(
        "/v1/auth/signup/complete/",
        {
            "role": "creator",
            "fullName": "Priya",
            "password": "StrongPassword123!",
        },
        format="json",
    )
    assert r3.status_code == 400
    assert "confirmPassword" in r3.json()["error"]["details"]

    # 4. confirm_password snake_case alias works
    r4 = client.post(
        "/v1/auth/signup/complete/",
        {
            "role": "creator",
            "fullName": "Priya Sharma",
            "displayName": "priya",
            "password": "StrongPassword123!",
            "confirm_password": "StrongPassword123!",
        },
        format="json",
    )
    assert r4.status_code == 201
    user = User.objects.get(phone="+919876500004")
    assert user.check_password("StrongPassword123!") is True
