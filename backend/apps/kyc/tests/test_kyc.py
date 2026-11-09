from datetime import timedelta
from typing import Any

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core import crypto
from apps.identity.models import AuditLog, User
from apps.kyc import services
from apps.kyc.models import KycProfile, KycStatus
from apps.uploads.models import Upload, UploadKind, UploadStatus
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db

PAN = {"pan": "abcde1234f", "nameOnPan": "Ananya Rao"}
BANK = {"accountHolderName": "Ananya Rao", "accountNumber": "123456789012", "ifsc": "hdfc0001234"}


def _doc(user: User, **overrides: Any) -> Upload:
    fields = dict(
        owner=user,
        kind=UploadKind.KYC_DOCUMENT,
        status=UploadStatus.READY,
        declared_content_type="application/pdf",
        declared_size=1000,
        staging_key=f"staging/{user.pk}/{overrides.get('kind', 'kyc')}-{Upload.objects.count()}",
        bucket="private",
        storage_key="kyc/doc.pdf",
    )
    fields.update(overrides)
    return Upload.objects.create(**fields)


def _complete_steps(client: APIClient) -> None:
    assert client.put("/v1/kyc/pan/", PAN, format="json").status_code == 200
    doc = _doc(client.user)
    assert client.put("/v1/kyc/id/", {"idType": "aadhaar", "uploadId": str(doc.pk)}, format="json").status_code == 200
    assert client.put("/v1/kyc/bank/", BANK, format="json").status_code == 200


@pytest.fixture
def new_creator(make_creator: Any) -> APIClient:
    return make_creator("Ananya Rao", kyc=False)


@pytest.fixture
def new_brand(make_brand: Any) -> APIClient:
    return make_brand("Fresh Co", kyc=False)


def test_full_flow_masks_and_encrypts(new_creator: APIClient) -> None:
    assert new_creator.get("/v1/auth/me/").json()["data"]["kycStatus"] == "not_started"
    empty = new_creator.get("/v1/kyc/").json()["data"]
    assert empty["status"] == "not_started" and empty["steps"]["pan"]["done"] is False

    pan = new_creator.put("/v1/kyc/pan/", PAN, format="json").json()["data"]
    assert pan["status"] == "in_progress"
    assert pan["steps"]["pan"] == {"done": True, "nameOnPan": "Ananya Rao", "panLast4": "234F"}

    _complete_steps(new_creator)
    body = new_creator.get("/v1/kyc/").json()["data"]
    assert body["steps"]["bank"] == {
        "done": True,
        "accountHolderName": "Ananya Rao",
        "accountLast4": "9012",
        "ifsc": "HDFC0001234",
    }
    raw = str(new_creator.get("/v1/kyc/").content)
    assert "ABCDE1234F" not in raw and "123456789012" not in raw

    kyc = KycProfile.objects.get(user=new_creator.user)
    assert "ABCDE1234F" not in kyc.pan_ciphertext and "123456789012" not in kyc.bank_account_ciphertext
    assert crypto.decrypt(kyc.pan_ciphertext, context=f"kyc.pan:{kyc.user_id}") == "ABCDE1234F"
    assert crypto.decrypt(kyc.bank_account_ciphertext, context=f"kyc.bank:{kyc.user_id}") == "123456789012"

    submitted = new_creator.post("/v1/kyc/submit/").json()["data"]
    assert submitted["status"] == "pending" and submitted["submittedAt"]
    assert new_creator.get("/v1/auth/me/").json()["data"]["kycStatus"] == "pending"
    assert AuditLog.objects.filter(action="kyc.submitted").count() == 1


def test_submit_requires_all_steps(new_creator: APIClient) -> None:
    new_creator.put("/v1/kyc/pan/", PAN, format="json")
    resp = new_creator.post("/v1/kyc/submit/")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "KYC_INCOMPLETE"
    assert "id, bank" in resp.json()["error"]["message"]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/kyc/pan/", {"pan": "ABCD1234F", "nameOnPan": "x"}),
        ("/v1/kyc/pan/", {"pan": "ABCDE1234F", "nameOnPan": "x", "extra": 1}),
        ("/v1/kyc/bank/", {**BANK, "accountNumber": "12ab"}),
        ("/v1/kyc/bank/", {**BANK, "ifsc": "HDFC1001234"}),
        ("/v1/kyc/id/", {"idType": "ration_card", "uploadId": "01a1bf0b-3478-7e53-a1a5-d8e538dbe7bc"}),
    ],
)
def test_input_validation(new_creator: APIClient, path: str, body: dict[str, Any]) -> None:
    resp = new_creator.put(path, body, format="json")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_id_document_must_be_own_ready_kyc_upload(new_creator: APIClient, make_brand: Any) -> None:
    other = make_brand()
    bad_docs = [
        _doc(other.user),  # someone else's
        _doc(new_creator.user, status=UploadStatus.PENDING),  # not verified
        _doc(new_creator.user, kind=UploadKind.CHAT_ATTACHMENT),  # wrong kind
    ]
    for doc in bad_docs:
        resp = new_creator.put("/v1/kyc/id/", {"idType": "passport", "uploadId": str(doc.pk)}, format="json")
        assert resp.status_code == 422 and resp.json()["error"]["code"] == "KYC_DOCUMENT_INVALID"


def test_locked_while_pending_and_after_verify(new_creator: APIClient, make_brand: Any) -> None:
    admin = User.objects.create_superuser(email="ops@example.com", password="x" * 20)
    _complete_steps(new_creator)
    new_creator.post("/v1/kyc/submit/")
    resp = new_creator.put("/v1/kyc/bank/", BANK, format="json")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "KYC_LOCKED"
    assert new_creator.post("/v1/kyc/submit/").status_code == 409

    kyc = KycProfile.objects.get(user=new_creator.user)
    services.review(actor=admin, kyc_id=kyc.pk, approve=True)
    assert new_creator.get("/v1/auth/me/").json()["data"]["kycStatus"] == "verified"
    assert "support" in new_creator.put("/v1/kyc/pan/", PAN, format="json").json()["error"]["message"]
    with pytest.raises(services.Conflict):
        services.review(actor=admin, kyc_id=kyc.pk, approve=False, reason="again")


def test_reject_then_resubmit(new_brand: APIClient) -> None:
    admin = User.objects.create_superuser(email="ops@example.com", password="x" * 20)
    _complete_steps(new_brand)
    new_brand.post("/v1/kyc/submit/")
    kyc = KycProfile.objects.get(user=new_brand.user)
    with pytest.raises(services.DomainError):
        services.review(actor=admin, kyc_id=kyc.pk, approve=False, reason="  ")
    services.review(actor=admin, kyc_id=kyc.pk, approve=False, reason="PAN name doesn't match the ID")

    body = new_brand.get("/v1/kyc/").json()["data"]
    assert body["status"] == "rejected" and body["rejectionReason"] == "PAN name doesn't match the ID"
    assert new_brand.get("/v1/notifications/").json()["data"][0]["type"] == "kyc_rejected"
    # fix a step and resubmit
    assert new_brand.put("/v1/kyc/pan/", {**PAN, "nameOnPan": "Fresh Co"}, format="json").status_code == 200
    resubmitted = new_brand.post("/v1/kyc/submit/").json()["data"]
    assert resubmitted["status"] == "pending" and resubmitted["rejectionReason"] is None


def test_kyc_is_private_and_role_gated(anon: APIClient, new_creator: APIClient, make_brand: Any) -> None:
    assert anon.get("/v1/kyc/").status_code == 401
    other = make_brand()
    new_creator.put("/v1/kyc/pan/", PAN, format="json")
    assert other.get("/v1/kyc/").json()["data"]["status"] == "verified"  # its own, not the creator's
    assert other.get("/v1/kyc/").json()["data"]["steps"]["pan"]["panLast4"] is None
    onboarding = User.objects.create_user(email="new@example.com")
    from conftest import _client_for

    assert _client_for(onboarding).get("/v1/kyc/").status_code == 403


def test_ciphertext_bound_to_context() -> None:
    token = crypto.encrypt("ABCDE1234F", context="kyc.pan:a")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token, context="kyc.pan:b")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt("v9:" + token.split(":", 1)[1], context="kyc.pan:a")


# --- gates: 403 KYC_REQUIRED until verified ------------------------------------------------------


def _assert_kyc_required(resp: Any) -> None:
    assert resp.status_code == 403, resp.json()
    assert resp.json()["error"]["code"] == "KYC_REQUIRED"


def test_gates_block_unverified_users(
    make_brand: Any, make_creator: Any, campaign_payload: dict[str, Any], package: Any
) -> None:
    brand, creator = make_brand("Verified"), make_creator("Verified Creator")
    new_brand, new_creator = make_brand("Unverified", kyc=False), make_creator("Unverified C", kyc=False)
    campaign = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]

    _assert_kyc_required(new_brand.post("/v1/campaigns/", campaign_payload, format="json"))
    bid = {"price": 800_000, "message": "hi", "deliveryDays": 5}
    _assert_kyc_required(new_creator.post(f"/v1/campaigns/{campaign['id']}/bids/", bid, format="json"))
    offer = {
        "creatorId": str(creator.user.creator.id),
        "deliverable": "1 Reel",
        "budget": 500_000,
        "deadline": (timezone.now() + timedelta(days=10)).date().isoformat(),
        "message": "Do a reel",
        "usageRights": USAGE_RIGHTS,
    }
    _assert_kyc_required(new_brand.post("/v1/offers/", offer, format="json"))
    new_brand.post("/v1/cart/", {"packageId": str(package.id)}, format="json")
    _assert_kyc_required(new_brand.post("/v1/cart/checkout/", {"usageRights": USAGE_RIGHTS}, format="json"))

    # a verified brand sends an offer; the unverified creator can decline but not accept
    sent = brand.post("/v1/offers/", {**offer, "creatorId": str(new_creator.user.creator.id)}, format="json")
    assert sent.status_code == 201, sent.json()
    oid = sent.json()["data"]["id"]
    _assert_kyc_required(new_creator.patch(f"/v1/creators/me/offers/{oid}/", {"status": "accepted"}, format="json"))
    declined = new_creator.patch(f"/v1/creators/me/offers/{oid}/", {"status": "rejected"}, format="json")
    assert declined.status_code == 200

    # verified users pass every gate
    assert creator.post(f"/v1/campaigns/{campaign['id']}/bids/", bid, format="json").status_code == 201


def test_kill_switch(make_brand: Any, campaign_payload: dict[str, Any]) -> None:
    new_brand = make_brand("Unverified", kyc=False)
    with override_settings(KYC_ENFORCED=False):
        assert new_brand.post("/v1/campaigns/", campaign_payload, format="json").status_code == 201
    assert KycStatus.VERIFIED != services.status_of(new_brand.user)


def test_admin_review_actions(new_creator: APIClient, new_brand: APIClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from django.contrib.admin.sites import AdminSite
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    from apps.kyc.admin import KycProfileAdmin
    from apps.uploads import storage

    admin_user = User.objects.create_superuser(email="ops@example.com", password="x" * 20)
    for client in (new_creator, new_brand):
        _complete_steps(client)
        client.post("/v1/kyc/submit/")
    request = RequestFactory().post("/")
    request.user = admin_user
    request.session = {}  # type: ignore[attr-defined]
    request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
    model_admin = KycProfileAdmin(KycProfile, AdminSite())

    creator_kyc = KycProfile.objects.filter(user=new_creator.user)
    brand_kyc = KycProfile.objects.filter(user=new_brand.user)
    model_admin.reject(request, brand_kyc)  # no reason typed yet -> error message, stays pending
    assert brand_kyc.get().status == KycStatus.PENDING
    brand_kyc.update(rejection_reason="Blurry ID")
    model_admin.reject(request, brand_kyc)
    model_admin.verify(request, creator_kyc)
    assert brand_kyc.get().status == KycStatus.REJECTED and creator_kyc.get().status == KycStatus.VERIFIED
    assert creator_kyc.get().reviewed_by == admin_user

    monkeypatch.setattr(storage, "signed_download_url", lambda b, p, e: f"https://storage.test/{b}/{p}?exp={e}")
    assert "exp=300" in model_admin.id_document_link(creator_kyc.get())
    assert model_admin.id_document_link(KycProfile(user=admin_user)) == "-"
    assert not model_admin.has_add_permission(request) and not model_admin.has_delete_permission(request)


def test_orders_need_both_parties_verified(
    make_brand: Any, make_creator: Any, campaign_payload: dict[str, Any]
) -> None:
    """Gate lives in create_order, so bid select / checkout / offer send can't skip the creator's KYC."""
    brand = make_brand("Verified")
    creator = make_creator("Was Verified")
    campaign = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    bid = creator.post(
        f"/v1/campaigns/{campaign['id']}/bids/", {"price": 800_000, "message": "hi", "deliveryDays": 5}, format="json"
    ).json()["data"]
    KycProfile.objects.filter(user=creator.user).update(status=KycStatus.REJECTED)  # e.g. ops revoked it

    resp = brand.post(f"/v1/campaigns/{campaign['id']}/bids/{bid['id']}/select/")
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "COUNTERPARTY_KYC_PENDING"
    assert "creator" in resp.json()["error"]["message"]

    # unverified creator's package can't be checked out either
    from apps.creators.models import CreatorPackage

    pkg = CreatorPackage.objects.create(
        creator=creator.user.creator,
        title="Reel",
        platform="instagram",
        deliverables="1",
        price_paise=1000,
        delivery_days=3,
    )
    brand.post("/v1/cart/", {"packageId": str(pkg.id)}, format="json")
    checkout = brand.post("/v1/cart/checkout/", {"usageRights": USAGE_RIGHTS}, format="json")
    assert checkout.status_code == 422 and checkout.json()["error"]["code"] == "COUNTERPARTY_KYC_PENDING"


def test_staff_cannot_review_own_kyc(make_creator: Any) -> None:
    client = make_creator("Staff Creator", kyc=False)
    _complete_steps(client)
    client.post("/v1/kyc/submit/")
    with pytest.raises(services.Forbidden):
        services.review(actor=client.user, kyc_id=KycProfile.objects.get(user=client.user).pk, approve=True)
