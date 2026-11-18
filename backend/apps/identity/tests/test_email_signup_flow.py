"""The frontend's email-OTP sign-up, replayed exactly: request -> verify (session) -> signup/complete, no phone."""

import pytest
from rest_framework.test import APIClient

from apps.identity import providers

pytestmark = pytest.mark.django_db

FRONTEND_PAYLOAD = {
    "role": "creator",
    "fullName": "User Name",
    "displayName": "User Name",
    "password": "abc@1234",
    "confirmPassword": "abc@1234",
}


@pytest.fixture
def sent_codes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    codes: list[str] = []
    monkeypatch.setattr(providers, "send_email_otp", lambda _dest, code: codes.append(code))
    return codes


def _verified_client(sent_codes: list[str], email: str = "new.user@example.com") -> APIClient:
    client = APIClient()
    req = client.post("/v1/auth/otp/request/", {"channel": "email", "destination": email}, format="json")
    assert req.status_code == 202, req.json()
    challenge = req.json()["data"]["challengeId"]
    ver = client.post("/v1/auth/otp/verify/", {"challengeId": challenge, "code": sent_codes[-1]}, format="json")
    assert ver.status_code == 200, ver.json()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {ver.json()['data']['access']}")
    return client


def test_creator_signup_without_phone_succeeds_with_bearer_session(sent_codes: list[str]) -> None:
    client = _verified_client(sent_codes)
    resp = client.post("/v1/auth/signup/complete/", FRONTEND_PAYLOAD, format="json")
    assert resp.status_code == 201, resp.json()
    assert resp.json()["data"]["role"] == "creator"


def test_second_creator_with_same_display_name_gets_unique_slug(sent_codes: list[str]) -> None:
    first = _verified_client(sent_codes, "a@example.com")
    assert first.post("/v1/auth/signup/complete/", FRONTEND_PAYLOAD, format="json").status_code == 201
    second = _verified_client(sent_codes, "b@example.com")
    resp = second.post("/v1/auth/signup/complete/", FRONTEND_PAYLOAD, format="json")
    assert resp.status_code == 201, resp.json()


def test_signup_without_bearer_is_401_not_500() -> None:
    resp = APIClient().post("/v1/auth/signup/complete/", FRONTEND_PAYLOAD, format="json")
    assert resp.status_code == 401


def test_unverified_phone_at_signup_is_not_attached(sent_codes: list[str]) -> None:
    """A phone is a login identifier: attaching one without an SMS code would let an attacker claim a
    victim's number, so the victim's later SMS sign-in would land in the attacker's account."""
    from apps.identity.models import User

    client = _verified_client(sent_codes, "attacker@example.com")
    resp = client.post("/v1/auth/signup/complete/", {**FRONTEND_PAYLOAD, "phone": "9876543210"}, format="json")
    assert resp.status_code == 201, resp.json()
    assert User.objects.get(email="attacker@example.com").phone is None
    assert not User.objects.filter(phone="+919876543210").exists()
