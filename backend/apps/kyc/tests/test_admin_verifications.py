from datetime import date

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.crypto import encrypt
from apps.identity.models import AdminRole, AuditLog, Role, User
from apps.identity.services import issue_tokens
from apps.kyc.models import (
    IdType,
    KycProfile,
    KycStatus,
    VerificationKind,
    VerificationRequest,
    VerificationStatus,
)
from apps.notifications.models import Notification
from apps.uploads.models import Upload, UploadKind, UploadStatus

pytestmark = pytest.mark.django_db


def _make_admin_client(role: str = AdminRole.TRUST_SAFETY) -> tuple[APIClient, User]:
    admin = User.objects.create_user(
        email=f"admin_{role}@linkloom.test",
        password="AdminPass123!",
        role=Role.ADMIN,
        admin_role=role,
        is_staff=True,
    )
    client = APIClient()
    tokens = issue_tokens(admin)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.access}")
    client.user = admin  # type: ignore[attr-defined]
    return client, admin


def _populate_kyc(user: User, pan: str = "ABCDE1234F", name: str = "Test User") -> KycProfile:
    import uuid

    up = Upload.objects.create(
        owner=user,
        kind=UploadKind.KYC_DOCUMENT,
        status=UploadStatus.READY,
        declared_content_type="application/pdf",
        declared_size=1024,
        staging_key=f"staging/kyc/{user.id}/{uuid.uuid4()}.pdf",
        bucket="private",
        storage_key=f"kyc/{user.id}/id.pdf",
        content_type="application/pdf",
        size_bytes=1024,
    )
    kyc, _ = KycProfile.objects.get_or_create(user=user)
    kyc.pan_name = name
    kyc.pan_last4 = pan[-4:]
    kyc.pan_ciphertext = encrypt(pan, context=f"kyc.pan:{user.pk}")
    kyc.id_type = IdType.AADHAAR
    kyc.id_document = up
    kyc.bank_holder_name = name
    kyc.bank_account_last4 = "4321"
    kyc.bank_account_ciphertext = encrypt("123456784321", context=f"kyc.bank:{user.pk}")
    kyc.bank_ifsc = "HDFC0001234"
    kyc.status = KycStatus.IN_PROGRESS
    kyc.save()
    return kyc


def test_creator_kyc_submit_creates_verification_record_and_sla(creator: APIClient) -> None:
    c_user = creator.user
    _make_admin_client(role=AdminRole.SUPER_ADMIN)  # to receive notification

    c_user.creator.dob = date(1995, 6, 15)
    c_user.creator.save()
    _populate_kyc(c_user, pan="ABCDE1234F", name=c_user.full_name)

    resp = creator.post("/v1/kyc/submit/")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["data"]["status"] == "pending"

    vr = VerificationRequest.objects.filter(user=c_user).first()
    assert vr is not None
    assert vr.kind == VerificationKind.CREATOR
    assert vr.status == VerificationStatus.PENDING
    assert vr.sla_due_at > vr.submitted_at
    # Creator SLA is ~24 hours
    diff_hours = (vr.sla_due_at - vr.submitted_at).total_seconds() / 3600
    assert 23.9 <= diff_hours <= 24.1

    # Checks verify age 18+ passed
    age_check = next((c for c in vr.checks if c["key"] == "age_18_plus"), None)
    assert age_check is not None
    assert age_check["result"] == "pass"
    assert age_check["blocking"] is False

    # Check notification sent to admin
    notif = Notification.objects.filter(type="verification_submitted").first()
    assert notif is not None
    assert notif.data["verificationId"] == str(vr.id)


def test_brand_kyc_submit_creates_verification_record_and_sla(brand: APIClient) -> None:
    b_user = brand.user
    b = b_user.brand
    b.gstin = "27AABCB1234F1Z5"
    b.company_pan = "AABCB1234F"
    b.billing_address = {"email": "hello@nimbufoods.com"}
    b.save()

    _populate_kyc(b_user, pan="AABCB1234F", name=b_user.full_name)

    resp = brand.post("/v1/kyc/submit/")
    assert resp.status_code == 200, resp.json()

    vr = VerificationRequest.objects.filter(user=b_user).first()
    assert vr is not None
    assert vr.kind == VerificationKind.BRAND
    # Brand SLA is ~48 hours
    diff_hours = (vr.sla_due_at - vr.submitted_at).total_seconds() / 3600
    assert 47.9 <= diff_hours <= 48.1

    # GSTIN PAN check
    gstin_check = next((c for c in vr.checks if c["key"] == "gstin_pan_match"), None)
    assert gstin_check is not None
    assert gstin_check["result"] == "pass"

    # Business email domain check
    email_check = next((c for c in vr.checks if c["key"] == "business_email_domain"), None)
    assert email_check is not None
    assert email_check["result"] == "pass"


def test_brand_extra_verification_fields_get_and_patch(brand: APIClient) -> None:
    # 1. Invalid PAN rejected
    bad_pan = brand.patch("/v1/brands/me/", {"companyPan": "INVALID"}, format="json")
    assert bad_pan.status_code == 400
    assert "companyPan" in bad_pan.json()["error"]["details"]

    # 2. Invalid CIN rejected
    bad_cin = brand.patch("/v1/brands/me/", {"cin": "12345"}, format="json")
    assert bad_cin.status_code == 400
    assert "cin" in bad_cin.json()["error"]["details"]

    # 3. Valid patch
    patch_resp = brand.patch(
        "/v1/brands/me/",
        {
            "companyPan": "AABCB1234F",
            "cin": "U12345MH2020PTC123456",
            "gstinStatus": "active",
            "regVerified": True,
            "signatoryName": "Anita Sharma",
            "signatoryDesignation": "Director",
            "billingEmail": "billing@nimbu.in",
        },
        format="json",
    )
    assert patch_resp.status_code == 200, patch_resp.json()
    data = patch_resp.json()["data"]
    # Check masking of company PAN
    assert data["companyPan"] == "XXXXXX234F"
    assert data["cin"] == "U12345MH2020PTC123456"
    assert data["gstinStatus"] == "active"
    assert data["regVerified"] is True
    assert data["signatoryName"] == "Anita Sharma"
    assert data["signatoryDesignation"] == "Director"
    assert data["billingEmail"] == "billing@nimbu.in"

    # 4. Check GET /v1/brands/me/
    get_resp = brand.get("/v1/brands/me/")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["companyPan"] == "XXXXXX234F"


def test_admin_verifications_list_filtering_and_counts(creator: APIClient, brand: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    # Submit KYC for creator and brand
    _populate_kyc(creator.user)
    _populate_kyc(brand.user)
    creator.post("/v1/kyc/submit/")
    brand.post("/v1/kyc/submit/")

    # List all
    r = admin_client.get("/v1/admin/verifications/")
    assert r.status_code == 200, r.json()
    res = r.json()
    assert res["success"] is True
    assert len(res["data"]) == 2
    counts = res["meta"]["counts"]
    assert counts["total"] == 2
    assert counts["pending"] == 2
    assert counts["creator"] == 1
    assert counts["brand"] == 1

    # Filter by kind
    r_creator = admin_client.get("/v1/admin/verifications/?kind=creator")
    assert r_creator.status_code == 200
    assert len(r_creator.json()["data"]) == 1
    assert r_creator.json()["data"][0]["kind"] == "creator"


def test_admin_verifications_detail_view(creator: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    _populate_kyc(creator.user)
    creator.post("/v1/kyc/submit/")

    vr = VerificationRequest.objects.get(user=creator.user)
    r = admin_client.get(f"/v1/admin/verifications/{vr.id}/")
    assert r.status_code == 200, r.json()
    data = r.json()["data"]
    assert data["id"] == str(vr.id)
    assert data["kind"] == "creator"
    assert data["kyc"]["pan"]["masked"] == "XXXXXX234F"
    assert data["kyc"]["bank"]["masked"] == "XXXXXX4321"
    assert len(data["checks"]) > 0


def test_admin_verification_approve_success_and_side_effects(creator: APIClient) -> None:
    admin_client, admin_user = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    creator.user.creator.dob = date(1998, 1, 1)
    creator.user.creator.approval_status = "pending"
    creator.user.creator.save()

    _populate_kyc(creator.user, name=creator.user.full_name)
    creator.post("/v1/kyc/submit/")

    vr = VerificationRequest.objects.get(user=creator.user)
    resp = admin_client.post(f"/v1/admin/verifications/{vr.id}/approve/")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["verification"]["status"] == "approved"
    assert "audit" in data and data["audit"]["hash"]

    # Verify KycProfile is VERIFIED
    vr.refresh_from_db()
    assert vr.status == VerificationStatus.APPROVED
    assert vr.kyc_profile.status == KycStatus.VERIFIED

    # Verify creator is approved
    creator.user.creator.refresh_from_db()
    assert creator.user.creator.approval_status == "approved"

    # Verify audit log was recorded
    audit_log = AuditLog.objects.filter(actor=admin_user, action="verification.approve").first()
    assert audit_log is not None
    assert audit_log.target == f"verification:{vr.id}"

    # Verify notification sent to user
    notif = Notification.objects.filter(user=creator.user, type="kyc_verified").first()
    assert notif is not None


def test_admin_verification_approve_refused_when_blocking_check_fails(creator: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    # Underage creator (born 2 years ago)
    creator.user.creator.dob = date(timezone.now().year - 2, 1, 1)
    creator.user.creator.save()

    _populate_kyc(creator.user)
    creator.post("/v1/kyc/submit/")

    vr = VerificationRequest.objects.get(user=creator.user)
    resp = admin_client.post(f"/v1/admin/verifications/{vr.id}/approve/")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "VERIFICATION_BLOCKED"

    vr.refresh_from_db()
    assert vr.status == VerificationStatus.PENDING


def test_admin_verification_reject_requires_reason_and_notifies(creator: APIClient) -> None:
    admin_client, admin_user = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    _populate_kyc(creator.user)
    creator.post("/v1/kyc/submit/")
    vr = VerificationRequest.objects.get(user=creator.user)

    # 1. Missing or too short reason
    r_bad = admin_client.post(f"/v1/admin/verifications/{vr.id}/reject/", {"reason": "no"}, format="json")
    assert r_bad.status_code == 400

    # 2. Valid reject
    r_ok = admin_client.post(
        f"/v1/admin/verifications/{vr.id}/reject/",
        {"reason": "Document blurred and illegible", "note": "Checked aadhaar back"},
        format="json",
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["data"]["verification"]["status"] == "rejected"

    vr.refresh_from_db()
    assert vr.status == VerificationStatus.REJECTED
    assert vr.kyc_profile.status == KycStatus.REJECTED
    assert vr.kyc_profile.rejection_reason == "Document blurred and illegible"

    # Notification sent
    notif = Notification.objects.filter(user=creator.user, type="kyc_rejected").first()
    assert notif is not None
    assert notif.data["reason"] == "Document blurred and illegible"


def test_admin_verification_request_info_sets_action_needed_and_user_reads_review(creator: APIClient) -> None:
    admin_client, admin_user = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    _populate_kyc(creator.user)
    creator.post("/v1/kyc/submit/")
    vr = VerificationRequest.objects.get(user=creator.user)

    # Admin requests info
    req_resp = admin_client.post(
        f"/v1/admin/verifications/{vr.id}/request-info/",
        {"items": ["id", "bank"], "message": "Please provide a clearer photo of your Aadhaar card"},
        format="json",
    )
    assert req_resp.status_code == 200, req_resp.json()
    assert req_resp.json()["data"]["verification"]["status"] == "info_requested"

    # Verify KycProfile is action_needed
    vr.refresh_from_db()
    assert vr.status == VerificationStatus.INFO_REQUESTED
    assert vr.kyc_profile.status == KycStatus.ACTION_NEEDED
    assert vr.kyc_profile.requested_items == ["id", "bank"]

    # User reads GET /v1/kyc/
    kyc_resp = creator.get("/v1/kyc/")
    assert kyc_resp.status_code == 200
    k_data = kyc_resp.json()["data"]
    assert k_data["status"] == "action_needed"
    assert k_data["review"] is not None
    assert k_data["review"]["requestedItems"] == ["id", "bank"]
    assert k_data["review"]["message"] == "Please provide a clearer photo of your Aadhaar card"

    # User can resubmit
    resubmit_resp = creator.post("/v1/kyc/submit/")
    assert resubmit_resp.status_code == 200
    assert resubmit_resp.json()["data"]["status"] == "pending"

    vr.refresh_from_db()
    assert vr.status == VerificationStatus.PENDING


def test_already_decided_verification_cannot_be_re_decided(creator: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    _populate_kyc(creator.user)
    creator.post("/v1/kyc/submit/")
    vr = VerificationRequest.objects.get(user=creator.user)

    # Reject first
    r1 = admin_client.post(f"/v1/admin/verifications/{vr.id}/reject/", {"reason": "First rejection"}, format="json")
    assert r1.status_code == 200

    # Try to approve already-rejected
    r_app = admin_client.post(f"/v1/admin/verifications/{vr.id}/approve/")
    assert r_app.status_code == 409
    assert r_app.json()["error"]["code"] == "ALREADY_DECIDED"

    # Try to reject again
    r_rej = admin_client.post(f"/v1/admin/verifications/{vr.id}/reject/", {"reason": "Second rejection"}, format="json")
    assert r_rej.status_code == 409
    assert r_rej.json()["error"]["code"] == "ALREADY_DECIDED"

    # Try to request info
    r_inf = admin_client.post(
        f"/v1/admin/verifications/{vr.id}/request-info/",
        {"items": ["pan"], "message": "More info"},
        format="json",
    )
    assert r_inf.status_code == 409
    assert r_inf.json()["error"]["code"] == "ALREADY_DECIDED"


def test_admin_verifications_permissions_and_role_checks(creator: APIClient) -> None:
    _populate_kyc(creator.user)
    creator.post("/v1/kyc/submit/")
    vr = VerificationRequest.objects.get(user=creator.user)

    # 1. Anonymous -> 401
    anon = APIClient()
    assert anon.get("/v1/admin/verifications/").status_code == 401
    assert anon.post(f"/v1/admin/verifications/{vr.id}/approve/").status_code == 401

    # 2. Regular user (creator) -> 403
    assert creator.get("/v1/admin/verifications/").status_code == 403
    assert creator.post(f"/v1/admin/verifications/{vr.id}/approve/").status_code == 403

    # 3. Admin with finance role -> 403
    fin_client, _ = _make_admin_client(role=AdminRole.FINANCE)
    assert fin_client.get("/v1/admin/verifications/").status_code == 403
    assert fin_client.post(f"/v1/admin/verifications/{vr.id}/approve/").status_code == 403

    # 4. Admin with trust_safety role -> 200
    ts_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    assert ts_client.get("/v1/admin/verifications/").status_code == 200

    # 5. Super admin -> 200 (hierarchy allows super_admin)
    sa_client, _ = _make_admin_client(role=AdminRole.SUPER_ADMIN)
    assert sa_client.get("/v1/admin/verifications/").status_code == 200
