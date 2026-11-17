"""Brand KYC: voluntary PAN + bank, never blocks a brand, verified only by an admin on the verification desk."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import AdminRole, Role, User
from apps.identity.services import issue_tokens
from apps.kyc.models import KycProfile, VerificationKind, VerificationRequest
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db

KYC = "/v1/brands/me/kyc/"
PAN = {"pan": "AABCP1234D"}
BANK = {"accountNumber": "123456789012", "ifsc": "SBIN0001234", "accountName": "Nimbu Foods Pvt Ltd"}


@pytest.fixture
def new_brand(make_brand: Any) -> APIClient:
    return make_brand("Nimbu Foods", kyc=False)


def _admin() -> APIClient:
    admin = User.objects.create_user(
        email="ts@example.com", role=Role.ADMIN, admin_role=AdminRole.TRUST_SAFETY, is_staff=True
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(admin).access}")
    return client


def _verification(user: User) -> VerificationRequest:
    return VerificationRequest.objects.get(user=user, kind=VerificationKind.BRAND)


def test_no_record_reads_as_pending_not_404(new_brand: APIClient) -> None:
    resp = new_brand.get(KYC)
    assert resp.status_code == 200, resp.json()
    assert resp.json()["data"] == {
        "status": "pending",
        "panVerified": False,
        "bankVerified": False,
        "rejectionReason": "",
    }


def test_saving_pan_submits_and_masks_nothing_back(new_brand: APIClient) -> None:
    resp = new_brand.post(f"{KYC}pan/", PAN, format="json")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["data"]["status"] == "submitted"
    assert resp.json()["data"]["panVerified"] is False
    assert "AABCP1234D" not in str(resp.json())  # never echoed back
    kyc = KycProfile.objects.get(user=new_brand.user)
    assert "AABCP1234D" not in kyc.pan_ciphertext and kyc.pan_last4 == "234D"
    assert _verification(new_brand.user).status == "pending"  # it's on the desk


def test_bank_alone_also_submits(new_brand: APIClient) -> None:
    resp = new_brand.post(f"{KYC}bank/", BANK, format="json")
    assert resp.status_code == 200, resp.json()
    assert resp.json()["data"]["status"] == "submitted"
    kyc = KycProfile.objects.get(user=new_brand.user)
    assert "123456789012" not in kyc.bank_account_ciphertext and kyc.bank_account_last4 == "9012"


def test_admin_approval_flips_only_the_fields_that_were_submitted(new_brand: APIClient) -> None:
    new_brand.post(f"{KYC}pan/", PAN, format="json")
    resp = _admin().post(f"/v1/admin/verifications/{_verification(new_brand.user).id}/approve/")
    assert resp.status_code == 200, resp.json()
    data = new_brand.get(KYC).json()["data"]
    assert data == {"status": "verified", "panVerified": True, "bankVerified": False, "rejectionReason": ""}


def test_changing_a_verified_detail_resets_it_and_goes_back_for_review(new_brand: APIClient) -> None:
    new_brand.post(f"{KYC}pan/", PAN, format="json")
    new_brand.post(f"{KYC}bank/", BANK, format="json")
    _admin().post(f"/v1/admin/verifications/{_verification(new_brand.user).id}/approve/")

    resp = new_brand.post(f"{KYC}bank/", {**BANK, "accountNumber": "999988887777"}, format="json")
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["status"] == "submitted" and data["panVerified"] is True and data["bankVerified"] is False
    assert VerificationRequest.objects.filter(user=new_brand.user, status="pending").exists()


def test_rejection_shows_the_reason(new_brand: APIClient) -> None:
    new_brand.post(f"{KYC}pan/", PAN, format="json")
    resp = _admin().post(
        f"/v1/admin/verifications/{_verification(new_brand.user).id}/reject/",
        {"reason": "PAN doesn't match the company name"},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    data = new_brand.get(KYC).json()["data"]
    assert data["status"] == "rejected" and data["rejectionReason"] == "PAN doesn't match the company name"
    assert data["panVerified"] is False
    # fixing it resubmits
    assert new_brand.post(f"{KYC}pan/", PAN, format="json").json()["data"]["status"] == "submitted"


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        ("pan/", {"pan": "ABC123"}, "pan"),
        ("bank/", {**BANK, "ifsc": "SBIN1234"}, "ifsc"),
        ("bank/", {**BANK, "accountNumber": "12ab"}, "accountNumber"),
        ("bank/", {"accountNumber": "123456789012", "ifsc": "SBIN0001234"}, "accountName"),
    ],
)
def test_bad_input_is_a_400_on_the_field(new_brand: APIClient, path: str, body: dict[str, Any], field: str) -> None:
    resp = new_brand.post(f"{KYC}{path}", body, format="json")
    assert resp.status_code == 400
    assert field in resp.json()["error"]["details"]


def test_pan_is_uppercased(new_brand: APIClient) -> None:
    assert new_brand.post(f"{KYC}pan/", {"pan": "aabcp1234d"}, format="json").status_code == 200


def test_creators_cant_use_brand_kyc(creator: APIClient) -> None:
    assert creator.get(KYC).status_code == 403


def test_unverified_brand_is_never_blocked(
    new_brand: APIClient, make_creator: Any, campaign_payload: dict[str, Any]
) -> None:
    creator = make_creator("Verified Creator")
    campaign = new_brand.post("/v1/campaigns/", campaign_payload, format="json")
    assert campaign.status_code == 201, campaign.json()
    offer = {
        "creatorId": str(creator.user.creator.id),
        "deliverable": "1 Reel",
        "budget": 500_000,
        "deadline": (timezone.now() + timedelta(days=10)).date().isoformat(),
        "message": "Do a reel",
        "usageRights": USAGE_RIGHTS,
    }
    sent = new_brand.post("/v1/offers/", offer, format="json")
    assert sent.status_code == 201, sent.json()
    # the creator (verified) accepts, which creates an order with an unverified brand
    accepted = creator.patch(
        f"/v1/creators/me/offers/{sent.json()['data']['id']}/", {"status": "accepted"}, format="json"
    )
    assert accepted.status_code == 200, accepted.json()
    assert accepted.json()["data"]["order"] is not None
