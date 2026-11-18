"""Admin API needs the MFA-stamped token from /v1/admin/auth/ (aal2). App sign-in tokens never reach it."""

import pytest
from rest_framework.test import APIClient

from apps.identity.models import AdminRole, Role, User
from apps.identity.services import issue_admin_tokens, issue_tokens

pytestmark = pytest.mark.django_db

ADMIN_URL = "/v1/admin/verifications/"


def _admin() -> User:
    return User.objects.create_user(
        email="ops@example.com",
        password="Strong-Pass-123",
        role=Role.ADMIN,
        admin_role=AdminRole.SUPER_ADMIN,
        is_staff=True,
    )


def _client(access: str) -> APIClient:
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return c


def test_app_login_token_of_an_admin_cannot_use_the_admin_api() -> None:
    admin = _admin()
    assert _client(issue_tokens(admin).access).get(ADMIN_URL).status_code == 403


def test_password_login_endpoint_token_cannot_use_the_admin_api() -> None:
    _admin()
    resp = APIClient().post(
        "/v1/auth/login/", {"identifier": "ops@example.com", "password": "Strong-Pass-123"}, format="json"
    )
    if resp.status_code == 200:  # whether or not app login is allowed for admins, its token must not work
        assert _client(resp.json()["data"]["access"]).get(ADMIN_URL).status_code == 403


def test_mfa_token_works() -> None:
    assert _client(issue_admin_tokens(_admin()).access).get(ADMIN_URL).status_code == 200
