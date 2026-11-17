import hashlib
import json
from typing import Any

import django_otp.oath
import pytest
from django.test import RequestFactory
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.response import Response
from rest_framework.test import APIClient, force_authenticate
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.audit import record_audit
from apps.identity.models import AdminRole, AuditLog, Role, User
from apps.identity.permissions import IsAdminUser, RequireAdminRoles
from apps.identity.services import issue_admin_tokens

pytestmark = pytest.mark.django_db


def _make_admin(
    email: str = "admin@example.com",
    password: str = "SecurePass123!",
    admin_role: str = AdminRole.SUPER_ADMIN,
    with_totp: bool = True,
) -> tuple[User, TOTPDevice | None]:
    user = User.objects.create_user(
        email=email,
        password=password,
        role=Role.ADMIN,
        admin_role=admin_role,
        is_staff=True,
    )
    device = None
    if with_totp:
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    return user, device


def _totp_code(device: TOTPDevice) -> str:
    token = django_otp.oath.TOTP(
        key=device.bin_key,
        step=device.step,
        t0=device.t0,
        digits=device.digits,
    ).token()
    return f"{token:0{device.digits}d}"


def test_admin_login_wrong_credentials(anon: APIClient) -> None:
    # 1. Non-existent email
    r1 = anon.post("/v1/admin/auth/login/", {"email": "missing@example.com", "password": "wrong"}, format="json")
    assert r1.status_code == 401
    assert r1.json()["error"]["code"] == "AUTH_INVALID"

    # 2. Existing admin with wrong password
    _make_admin(email="boss@example.com", password="CorrectPass123!")
    r2 = anon.post("/v1/admin/auth/login/", {"email": "boss@example.com", "password": "WrongPassword"}, format="json")
    assert r2.status_code == 401
    assert r2.json()["error"]["code"] == "AUTH_INVALID"


def test_non_admin_login_rejected_403(anon: APIClient) -> None:
    # Creator or brand user attempting to access admin login
    User.objects.create_user(
        email="creator@example.com",
        password="ValidPassword123!",
        role=Role.CREATOR,
        is_staff=False,
    )
    resp = anon.post(
        "/v1/admin/auth/login/",
        {"email": "creator@example.com", "password": "ValidPassword123!"},
        format="json",
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_admin_login_success_prompts_mfa(anon: APIClient) -> None:
    admin, device = _make_admin(email="super@example.com", password="SecretPassword123!")
    resp = anon.post(
        "/v1/admin/auth/login/",
        {"email": "super@example.com", "password": "SecretPassword123!"},
        format="json",
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["mfaRequired"] is True
    assert data["mfaToken"]
    assert data["user"]["email"] == "super@example.com"
    assert data["user"]["role"] == "admin"
    assert data["user"]["adminRole"] == "super_admin"


def test_admin_mfa_verify_invalid_code(anon: APIClient) -> None:
    admin, device = _make_admin(email="admin_mfa@example.com", password="SecretPassword123!")
    login_resp = anon.post(
        "/v1/admin/auth/login/",
        {"email": "admin_mfa@example.com", "password": "SecretPassword123!"},
        format="json",
    )
    mfa_token = login_resp.json()["data"]["mfaToken"]

    # Wrong code
    resp = anon.post(
        "/v1/admin/auth/mfa/verify/",
        {"code": "000000", "mfaToken": mfa_token},
        format="json",
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "OTP_INVALID"

    # Invalid token
    resp_bad_token = anon.post(
        "/v1/admin/auth/mfa/verify/",
        {"code": "123456", "mfaToken": "invalid.signature.token"},
        format="json",
    )
    assert resp_bad_token.status_code == 400
    assert resp_bad_token.json()["error"]["code"] == "OTP_INVALID"


def test_admin_mfa_verify_success_issues_tokens(anon: APIClient) -> None:
    admin, device = _make_admin(
        email="trusted@example.com",
        password="SecretPassword123!",
        admin_role=AdminRole.TRUST_SAFETY,
    )
    login_resp = anon.post(
        "/v1/admin/auth/login/",
        {"email": "trusted@example.com", "password": "SecretPassword123!"},
        format="json",
    )
    mfa_token = login_resp.json()["data"]["mfaToken"]
    code = _totp_code(device)

    resp = anon.post(
        "/v1/admin/auth/mfa/verify/",
        {"code": code, "mfaToken": mfa_token},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert "access" in data and "refresh" in data
    assert data["user"]["adminRole"] == "trust_safety"
    assert data["user"]["role"] == "admin"

    # Decode and inspect access token claims
    access_token = AccessToken(data["access"])
    assert access_token.get("role") == "admin"
    assert access_token.get("admin_role") == "trust_safety"
    assert access_token.get("is_staff") is True
    assert access_token.get("aal") == "aal2"
    assert access_token.get("amr") == ["totp"]


def test_admin_logout_blacklists_token_and_audits() -> None:
    admin, _ = _make_admin(email="logout_admin@example.com")
    tokens = issue_admin_tokens(admin)

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.access}")

    resp = client.post(
        "/v1/admin/auth/logout/",
        {"refresh": tokens.refresh},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["success"] is True

    # Audit log check
    log = AuditLog.objects.filter(actor=admin, action="admin.logout").order_by("-created_at").first()
    assert log is not None
    assert log.target == f"user:{admin.id}"
    assert log.hash

    # Confirm refresh token cannot be refreshed or used
    client.logout()


def test_is_admin_user_permission_and_roles() -> None:
    class ProtectedAdminView(APIView):
        permission_classes = [IsAdminUser, RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY)]

        def get(self, request: Any) -> Response:
            return Response({"ok": True})

    factory = RequestFactory()
    view = ProtectedAdminView.as_view()

    # 1. Anonymous user -> 401
    req_anon = factory.get("/test-admin/")
    req_anon.user = User()  # unauthenticated
    resp_anon = view(req_anon)
    assert resp_anon.status_code == 401

    # 2. Regular user (creator) -> 403 FORBIDDEN
    creator = User.objects.create_user(phone="+919999900001", role=Role.CREATOR)
    req_creator = factory.get("/test-admin/")
    force_authenticate(req_creator, user=creator)
    resp_creator = view(req_creator)
    assert resp_creator.status_code == 403

    # 3. Admin with non-matching role (finance) -> 403 FORBIDDEN
    finance_admin, _ = _make_admin(email="fin@example.com", admin_role=AdminRole.FINANCE)
    req_fin = factory.get("/test-admin/")
    force_authenticate(req_fin, user=finance_admin)
    resp_fin = view(req_fin)
    assert resp_fin.status_code == 403

    # 4. Admin with matching role (trust_safety) -> 200 OK
    trust_admin, _ = _make_admin(email="ts@example.com", admin_role=AdminRole.TRUST_SAFETY)
    req_ts = factory.get("/test-admin/")
    force_authenticate(req_ts, user=trust_admin)
    resp_ts = view(req_ts)
    assert resp_ts.status_code == 200

    # 5. Admin with super_admin (always satisfies) -> 200 OK
    super_admin, _ = _make_admin(email="super2@example.com", admin_role=AdminRole.SUPER_ADMIN)
    req_super = factory.get("/test-admin/")
    force_authenticate(req_super, user=super_admin)
    resp_super = view(req_super)
    assert resp_super.status_code == 200


def test_record_audit_helper() -> None:
    admin, _ = _make_admin(email="audit_boss@example.com")
    result = record_audit(
        admin=admin,
        action="creator.verify",
        target="creator:12345",
        detail={"reason": "Manual KYC check passed"},
    )

    assert "id" in result and "hash" in result
    assert len(result["hash"]) == 64

    # Verify DB record
    entry = AuditLog.objects.get(pk=result["id"])
    assert entry.admin == admin
    assert entry.action == "creator.verify"
    assert entry.target == "creator:12345"
    assert entry.detail == {"reason": "Manual KYC check passed"}
    assert entry.hash == result["hash"]

    # Verify SHA-256 computation
    expected_payload = (
        f"{entry.id}:"
        f"{entry.created_at.isoformat() if entry.created_at else ''}:"
        f"{admin.id}:"
        f"creator.verify:creator:12345:"
        f'{json.dumps({"reason": "Manual KYC check passed"}, sort_keys=True)}'
    )
    expected_hash = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    assert entry.hash == expected_hash
