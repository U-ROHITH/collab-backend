import pytest
from rest_framework.test import APIClient

from apps.identity.models import User
from apps.identity.services import complete_signup, issue_tokens

pytestmark = pytest.mark.django_db


def test_email_verification_challenge_returns_expires_at(anon: APIClient) -> None:
    user = User.objects.create_user(phone="+919876543210")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(user).access}")

    resp = client.post("/v1/auth/email/request/", {"email": "hello@brand.com"}, format="json")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert "challengeId" in data
    assert "expiresAt" in data
    assert "expiresIn" in data


def test_me_endpoint_returns_email_and_role_and_email_change_resets_verification() -> None:
    user = User.objects.create_user(email="original@brand.com", phone="+919876543211")
    complete_signup(user=user, role="brand", full_name="Brand User", brand_name="Brand Co")
    user.email_verified = True
    user.save(update_fields=["email_verified"])

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(user).access}")

    # Check me endpoint exposes email, emailVerified, and role
    me_resp = client.get("/v1/auth/me/")
    assert me_resp.status_code == 200
    me = me_resp.json()["data"]
    assert me["email"] == "original@brand.com"
    assert me["emailVerified"] is True
    assert me["role"] == "brand"

    # Now change email via PATCH /v1/auth/me/
    patch_resp = client.patch("/v1/auth/me/", {"email": "updated@brand.com"}, format="json")
    assert patch_resp.status_code == 200
    updated_me = patch_resp.json()["data"]
    assert updated_me["email"] == "updated@brand.com"
    assert updated_me["emailVerified"] is False, "Changing email must reset emailVerified to False"

    # Direct model save email change also resets email_verified
    user.refresh_from_db()
    assert user.email_verified is False
    user.email_verified = True
    user.save(update_fields=["email_verified"])

    user.email = "another@brand.com"
    user.save()
    assert user.email_verified is False


def test_refresh_token_failure_returns_token_expired(anon: APIClient) -> None:
    resp = anon.post("/v1/auth/token/refresh/", {"refresh": "invalid_or_expired_token"}, format="json")
    assert resp.status_code == 401
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "TOKEN_EXPIRED"
