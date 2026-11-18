"""Both review paths (API desk and Django admin) apply the same rules and the same side effects."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.brands.models import Brand
from apps.core.exceptions import Conflict
from apps.creators.models import ApprovalStatus
from apps.identity.models import AdminRole, Role, User
from apps.identity.services import issue_admin_tokens
from apps.kyc import services
from apps.kyc.models import KycProfile, KycStatus, VerificationRequest
from apps.kyc.tests.test_admin_verifications import _populate_kyc

pytestmark = pytest.mark.django_db


def _staff() -> User:
    return User.objects.create_user(
        email="ts@example.com", role=Role.ADMIN, admin_role=AdminRole.TRUST_SAFETY, is_staff=True
    )


def _submitted(user: User) -> tuple[KycProfile, VerificationRequest]:
    _populate_kyc(user, name=user.full_name or "Test User")
    services.submit(user=user)
    kyc = KycProfile.objects.get(user=user)
    return kyc, VerificationRequest.objects.get(kyc_profile=kyc)


def test_django_admin_approval_respects_blocking_checks(make_creator: Any) -> None:
    creator = make_creator("Ananya Rao", approved=False, kyc=False)
    kyc, vr = _submitted(creator.user)
    vr.checks = [{"key": "age_18_plus", "result": "fail", "blocking": True, "text": "under 18"}]
    vr.save(update_fields=["checks"])
    with pytest.raises(Conflict):
        services.review(actor=_staff(), kyc_id=kyc.pk, approve=True)
    kyc.refresh_from_db()
    assert kyc.status == KycStatus.PENDING


def test_django_admin_approval_syncs_the_desk_and_approves_the_creator(make_creator: Any) -> None:
    creator = make_creator("Ananya Rao", approved=False, kyc=False)
    kyc, vr = _submitted(creator.user)
    vr.checks = []
    vr.save(update_fields=["checks"])
    services.review(actor=_staff(), kyc_id=kyc.pk, approve=True)
    vr.refresh_from_db()
    creator.user.creator.refresh_from_db()
    assert vr.status == "approved" and vr.decided_by is not None
    assert creator.user.creator.approval_status == ApprovalStatus.APPROVED


def _approve_on_desk(vr: VerificationRequest) -> Any:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_admin_tokens(_staff()).access}")
    return client.post(f"/v1/admin/verifications/{vr.id}/approve/")


def test_brand_without_gstin_is_not_marked_gstin_active(make_brand: Any) -> None:
    brand = make_brand("No GST Co", kyc=False)
    brand.post("/v1/brands/me/kyc/pan/", {"pan": "AABCP1234D"}, format="json")
    vr = VerificationRequest.objects.get(user=brand.user)
    assert _approve_on_desk(vr).status_code == 200
    b = Brand.objects.get(owner=brand.user)
    assert b.reg_verified is False and b.gstin_status != "active"


def test_brand_with_matching_gstin_is_marked_verified(make_brand: Any) -> None:
    brand = make_brand("GST Co", kyc=False)
    Brand.objects.filter(owner=brand.user).update(gstin="27AABCP1234D1Z5")
    brand.post("/v1/brands/me/kyc/pan/", {"pan": "AABCP1234D"}, format="json")
    vr = VerificationRequest.objects.get(user=brand.user)
    assert _approve_on_desk(vr).status_code == 200, vr.checks
    b = Brand.objects.get(owner=brand.user)
    assert b.reg_verified is True and b.gstin_status == "active"
