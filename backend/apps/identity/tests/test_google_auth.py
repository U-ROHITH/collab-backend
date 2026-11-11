from unittest.mock import patch

import pytest
from django.db import IntegrityError
from rest_framework.test import APIClient

from apps.identity.models import AuditLog, User, UserStatus


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.mark.django_db
class TestGoogleAuthEndpoint:
    url = "/v1/auth/google/"

    @patch("apps.identity.services.verify_google_id_token")
    def test_google_signup_new_user(self, mock_verify, client):
        mock_verify.return_value = {
            "email": "googleuser@example.com",
            "email_verified": True,
            "name": "Google User",
            "sub": "1234567890",
        }

        resp = client.post(self.url, {"idToken": "valid_token", "role": "creator"}, format="json")
        assert resp.status_code == 200
        data = resp.json()["data"]

        assert "access" in data
        assert "refresh" in data
        assert data["user"]["email"] == "googleuser@example.com"
        assert data["user"]["emailVerified"] is True
        assert data["user"]["fullName"] == "Google User"
        assert data["user"]["needsOnboarding"] is True

        user = User.objects.get(email="googleuser@example.com")
        assert user.email_verified is True
        assert user.full_name == "Google User"
        assert user.phone is None

        audit = AuditLog.objects.filter(actor=user, action="user.login_google").first()
        assert audit is not None
        assert audit.metadata == {"email": "googleuser@example.com"}

    @patch("apps.identity.services.verify_google_id_token")
    def test_google_login_existing_user(self, mock_verify, client):
        existing_user = User.objects.create_user(
            email="existing@example.com",
            full_name="Existing Name",
            email_verified=False,
        )

        mock_verify.return_value = {
            "email": "existing@example.com",
            "email_verified": True,
            "name": "Updated Google Name",
            "sub": "1234567890",
        }

        resp = client.post(self.url, {"idToken": "valid_token"}, format="json")
        assert resp.status_code == 200
        data = resp.json()["data"]

        assert data["user"]["id"] == str(existing_user.id)
        assert data["user"]["emailVerified"] is True

        existing_user.refresh_from_db()
        assert existing_user.email_verified is True

    @patch("apps.identity.services.verify_google_id_token")
    def test_supports_snake_case_id_token(self, mock_verify, client):
        mock_verify.return_value = {
            "email": "snakecase@example.com",
            "email_verified": True,
            "name": "Snake Case",
        }

        resp = client.post(self.url, {"id_token": "valid_token"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["data"]["user"]["email"] == "snakecase@example.com"

    @patch("apps.identity.services.verify_google_id_token")
    def test_google_auth_token_invalid(self, mock_verify, client):
        from apps.identity.services import GoogleTokenInvalid

        mock_verify.side_effect = GoogleTokenInvalid()

        resp = client.post(self.url, {"idToken": "bad_token"}, format="json")
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "GOOGLE_TOKEN_INVALID"

    @patch("apps.identity.services.verify_google_id_token")
    def test_google_auth_email_unverified(self, mock_verify, client):
        from apps.identity.services import GoogleEmailUnverified

        mock_verify.side_effect = GoogleEmailUnverified()

        resp = client.post(self.url, {"idToken": "unverified_email_token"}, format="json")
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "GOOGLE_EMAIL_UNVERIFIED"

    @patch("apps.identity.services.verify_google_id_token")
    def test_google_auth_suspended_user_forbidden(self, mock_verify, client):
        User.objects.create_user(
            email="suspended@example.com",
            status=UserStatus.SUSPENDED,
        )

        mock_verify.return_value = {
            "email": "suspended@example.com",
            "email_verified": True,
            "name": "Suspended User",
        }

        resp = client.post(self.url, {"idToken": "token"}, format="json")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "ACCOUNT_SUSPENDED"

    @patch("apps.identity.services.verify_google_id_token")
    def test_google_auth_deactivated_user_forbidden(self, mock_verify, client):
        User.objects.create_user(
            email="deactivated@example.com",
            status=UserStatus.DEACTIVATED,
        )

        mock_verify.return_value = {
            "email": "deactivated@example.com",
            "email_verified": True,
            "name": "Deactivated User",
        }

        resp = client.post(self.url, {"idToken": "token"}, format="json")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "ACCOUNT_DEACTIVATED"

    @patch("apps.identity.services.verify_google_id_token")
    @patch("apps.identity.models.User.objects.create_user")
    def test_google_auth_concurrent_race_condition(self, mock_create, mock_verify, client):
        existing_user = User.objects.create(email="race@example.com")
        mock_create.side_effect = IntegrityError("duplicate key")

        mock_verify.return_value = {
            "email": "race@example.com",
            "email_verified": True,
            "name": "Race User",
        }

        resp = client.post(self.url, {"idToken": "valid_token"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["data"]["user"]["id"] == str(existing_user.id)


@pytest.mark.django_db
class TestVerifyGoogleIdTokenHelper:
    @patch("google.oauth2.id_token.verify_oauth2_token")
    def test_verify_google_id_token_success(self, mock_verify):
        from apps.identity.services import verify_google_id_token

        mock_verify.return_value = {
            "email": "test@example.com",
            "email_verified": True,
            "name": "Test User",
        }

        info = verify_google_id_token("good_token")
        assert info["email"] == "test@example.com"
        assert info["email_verified"] is True

    @patch("google.oauth2.id_token.verify_oauth2_token")
    def test_verify_google_id_token_unverified_email(self, mock_verify):
        from apps.identity.services import GoogleEmailUnverified, verify_google_id_token

        mock_verify.return_value = {
            "email": "test@example.com",
            "email_verified": False,
        }

        with pytest.raises(GoogleEmailUnverified):
            verify_google_id_token("token_unverified")

    @patch("google.oauth2.id_token.verify_oauth2_token")
    def test_verify_google_id_token_missing_email(self, mock_verify):
        from apps.identity.services import GoogleTokenInvalid, verify_google_id_token

        mock_verify.return_value = {
            "name": "No Email",
            "email_verified": True,
        }

        with pytest.raises(GoogleTokenInvalid):
            verify_google_id_token("token_no_email")

    @patch("google.oauth2.id_token.verify_oauth2_token")
    def test_verify_google_id_token_invalid_signature_raises(self, mock_verify):
        from apps.identity.services import GoogleTokenInvalid, verify_google_id_token

        mock_verify.side_effect = ValueError("Invalid token")

        with pytest.raises(GoogleTokenInvalid):
            verify_google_id_token("bad_signature")
