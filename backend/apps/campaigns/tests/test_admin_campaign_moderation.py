import uuid
from typing import Any

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.campaigns.models import Bid, BidStatus
from apps.identity.models import AdminRole, Role, User
from apps.identity.services import issue_admin_tokens
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


def _make_admin_client(role: str = AdminRole.TRUST_SAFETY) -> tuple[APIClient, User]:
    admin = User.objects.create_user(
        email=f"admin_{role}_{uuid.uuid4().hex[:6]}@linkloom.test",
        password="AdminPass123!",
        role=Role.ADMIN,
        admin_role=role,
        is_staff=True,
    )
    client = APIClient()
    tokens = issue_admin_tokens(admin)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.access}")
    client.user = admin  # type: ignore[attr-defined]
    return client, admin


@override_settings(CAMPAIGN_MODERATION_ENFORCED=True)
def test_campaign_creation_sets_pending_review_and_notifies_admins(
    brand: APIClient, campaign_payload: dict[str, Any]
) -> None:
    _, admin = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    resp = brand.post("/v1/campaigns/", campaign_payload, format="json")
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["moderationStatus"] == "pending_review"
    assert data["moderation"]["status"] == "pending_review"
    assert data["moderation"]["changedFields"] == []
    assert data["moderation"]["reason"] is None

    # Check notification for admin
    notif = Notification.objects.filter(user=admin, type="campaign_submitted").first()
    assert notif is not None
    assert str(data["id"]) in str(notif.data.get("campaignId"))


@override_settings(CAMPAIGN_MODERATION_ENFORCED=True)
def test_pending_campaign_not_visible_to_creators_or_guests(
    brand: APIClient, creator: APIClient, anon: APIClient, campaign_payload: dict[str, Any]
) -> None:
    resp = brand.post("/v1/campaigns/", campaign_payload, format="json")
    assert resp.status_code == 201
    cid = resp.json()["data"]["id"]

    # Creator list shouldn't include pending campaign
    creator_list = creator.get("/v1/campaigns/").json()["data"]
    assert cid not in [c["id"] for c in creator_list]

    # Creator detail returns 404
    assert creator.get(f"/v1/campaigns/{cid}/").status_code == 404

    # Guest list and detail return 404 / excluded
    guest_list = anon.get("/v1/campaigns/").json()["data"]
    assert cid not in [c["id"] for c in guest_list]
    assert anon.get(f"/v1/campaigns/{cid}/").status_code == 404

    # Brand CAN see own pending campaign
    brand_detail = brand.get(f"/v1/campaigns/{cid}/")
    assert brand_detail.status_code == 200
    assert brand_detail.json()["data"]["moderationStatus"] == "pending_review"


@override_settings(CAMPAIGN_MODERATION_ENFORCED=True)
def test_bidding_on_pending_campaign_fails_with_409(
    brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]
) -> None:
    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    cid = c["id"]

    bid_resp = creator.post(
        f"/v1/campaigns/{cid}/bids/",
        {"price": 500_000, "message": "Can do this", "deliveryDays": 3},
        format="json",
    )
    assert bid_resp.status_code == 409
    assert bid_resp.json()["error"]["code"] == "CAMPAIGN_NOT_LIVE"


@override_settings(CAMPAIGN_MODERATION_ENFORCED=True)
def test_admin_campaign_list_and_filters(brand: APIClient, campaign_payload: dict[str, Any]) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    c1 = brand.post("/v1/campaigns/", {**campaign_payload, "title": "Pending Camp 1"}, format="json").json()["data"]
    c2 = brand.post("/v1/campaigns/", {**campaign_payload, "title": "Pending Camp 2"}, format="json").json()["data"]

    # Admin list pending (default)
    list_resp = admin_client.get("/v1/admin/campaigns/")
    assert list_resp.status_code == 200
    res = list_resp.json()
    items = res["data"]
    assert any(i["id"] == c1["id"] for i in items)
    assert any(i["id"] == c2["id"] for i in items)
    assert res["meta"]["counts"]["pending"] >= 2

    # Approve one
    admin_client.post(f"/v1/admin/campaigns/{c1['id']}/approve/")

    # Check live filter
    live_resp = admin_client.get("/v1/admin/campaigns/?filter=live")
    live_items = live_resp.json()["data"]
    assert any(i["id"] == c1["id"] for i in live_items)
    assert not any(i["id"] == c2["id"] for i in live_items)


@override_settings(CAMPAIGN_MODERATION_ENFORCED=True)
def test_admin_campaign_detail_and_signals(brand: APIClient, campaign_payload: dict[str, Any]) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    payload = {
        **campaign_payload,
        "title": "Call 9876543210 for collab",
        "brief": "Reach out on WhatsApp or pay via GPay directly",
    }
    c = brand.post("/v1/campaigns/", payload, format="json").json()["data"]

    detail_resp = admin_client.get(f"/v1/admin/campaigns/{c['id']}/")
    assert detail_resp.status_code == 200
    data = detail_resp.json()["data"]

    assert data["id"] == c["id"]
    assert "brand" in data
    assert "bidsSummary" in data
    assert "signals" in data
    signals = {s["key"]: s for s in data["signals"]}
    assert "solicitation_check" in signals
    # Phone, WhatsApp, and GPay should trigger warning
    assert signals["solicitation_check"]["result"] == "warn"


@override_settings(CAMPAIGN_MODERATION_ENFORCED=True)
def test_admin_approve_campaign_and_audit(
    brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]
) -> None:
    admin_client, admin_user = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    cid = c["id"]

    approve_resp = admin_client.post(f"/v1/admin/campaigns/{cid}/approve/")
    assert approve_resp.status_code == 200
    res_data = approve_resp.json()["data"]
    assert res_data["campaign"]["moderationStatus"] == "live"
    assert "audit" in res_data
    assert res_data["audit"]["id"]
    assert res_data["audit"]["hash"]

    # Campaign is now visible to creator
    creator_list = creator.get("/v1/campaigns/").json()["data"]
    assert cid in [x["id"] for x in creator_list]

    # Creator can now place a bid
    bid_resp = creator.post(
        f"/v1/campaigns/{cid}/bids/",
        {"price": 400_000, "message": "Ready to work", "deliveryDays": 4},
        format="json",
    )
    assert bid_resp.status_code == 201

    # Cannot approve again (already live) -> 409
    dup = admin_client.post(f"/v1/admin/campaigns/{cid}/approve/")
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"


@override_settings(CAMPAIGN_MODERATION_ENFORCED=True)
def test_admin_reject_campaign(brand: APIClient, campaign_payload: dict[str, Any]) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    cid = c["id"]

    # Short reason fails validation (min 4 chars)
    bad_resp = admin_client.post(f"/v1/admin/campaigns/{cid}/reject/", {"reason": "no"}, format="json")
    assert bad_resp.status_code == 400

    # Valid rejection
    reject_resp = admin_client.post(
        f"/v1/admin/campaigns/{cid}/reject/",
        {"reason": "Inappropriate brief content", "ruleBroken": "COMMUNITY_STANDARDS"},
        format="json",
    )
    assert reject_resp.status_code == 200
    res_data = reject_resp.json()["data"]
    assert res_data["campaign"]["moderationStatus"] == "rejected"
    assert res_data["campaign"]["moderation"]["reason"] == "Inappropriate brief content"
    assert res_data["campaign"]["moderation"]["ruleBroken"] == "COMMUNITY_STANDARDS"

    # Owning brand sees rejection reason
    brand_camp = brand.get(f"/v1/campaigns/{cid}/").json()["data"]
    assert brand_camp["moderation"]["status"] == "rejected"
    assert brand_camp["moderation"]["reason"] == "Inappropriate brief content"

    # Brand edits rejected campaign -> moves back to pending_review
    patch_resp = brand.patch(f"/v1/campaigns/{cid}/", {"brief": "Cleaned up brief content"}, format="json")
    assert patch_resp.status_code == 200
    assert patch_resp.json()["data"]["moderationStatus"] == "pending_review"
    assert patch_resp.json()["data"]["moderation"]["reason"] is None


@override_settings(CAMPAIGN_MODERATION_ENFORCED=True)
def test_live_campaign_edit_freezes_bids_and_reapproval_unfreezes(
    brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]
) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    cid = c["id"]

    # Approve to live
    admin_client.post(f"/v1/admin/campaigns/{cid}/approve/")

    # Creator bids
    bid_resp = creator.post(
        f"/v1/campaigns/{cid}/bids/",
        {"price": 400_000, "message": "I will deliver", "deliveryDays": 4},
        format="json",
    )
    bid_id = bid_resp.json()["data"]["id"]

    # Brand edits reviewed field ('title')
    edit_resp = brand.patch(f"/v1/campaigns/{cid}/", {"title": "Updated Brand Title"}, format="json")
    assert edit_resp.status_code == 200
    assert edit_resp.json()["data"]["moderationStatus"] == "pending_review"
    assert "title" in edit_resp.json()["data"]["moderation"]["changedFields"]

    # Check that bid was shifted to HELD
    bid = Bid.objects.get(pk=bid_id)
    assert bid.status == BidStatus.HELD

    # Selecting held bid fails with 409
    sel_resp = brand.post(f"/v1/campaigns/{cid}/bids/{bid_id}/select/")
    assert sel_resp.status_code == 409
    assert sel_resp.json()["error"]["code"] == "BID_NOT_PENDING"

    # Admin re-approves
    reapp_resp = admin_client.post(f"/v1/admin/campaigns/{cid}/approve/")
    assert reapp_resp.status_code == 200
    assert reapp_resp.json()["data"]["campaign"]["moderationStatus"] == "live"

    # Bid is unfrozen back to PENDING
    bid.refresh_from_db()
    assert bid.status == BidStatus.PENDING

    # Now selecting bid succeeds
    sel_ok = brand.post(f"/v1/campaigns/{cid}/bids/{bid_id}/select/")
    assert sel_ok.status_code == 201


@override_settings(CAMPAIGN_MODERATION_ENFORCED=True)
def test_admin_hide_and_restore_campaign(
    brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]
) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    cid = c["id"]

    # Cannot hide pending campaign
    assert (
        admin_client.post(f"/v1/admin/campaigns/{cid}/hide/", {"reason": "test"}, format="json").status_code == 409
    )

    # Approve
    admin_client.post(f"/v1/admin/campaigns/{cid}/approve/")

    # Creator bids
    bid_resp = creator.post(
        f"/v1/campaigns/{cid}/bids/",
        {"price": 400_000, "message": "My bid", "deliveryDays": 3},
        format="json",
    )
    bid_id = bid_resp.json()["data"]["id"]

    # Admin hides campaign
    hide_resp = admin_client.post(
        f"/v1/admin/campaigns/{cid}/hide/", {"reason": "Temporary investigation"}, format="json"
    )
    assert hide_resp.status_code == 200
    assert hide_resp.json()["data"]["campaign"]["moderationStatus"] == "hidden"

    # Bid is held
    bid = Bid.objects.get(pk=bid_id)
    assert bid.status == BidStatus.HELD

    # Hidden campaign not visible to creator
    assert creator.get(f"/v1/campaigns/{cid}/").status_code == 404

    # Restore campaign
    restore_resp = admin_client.post(
        f"/v1/admin/campaigns/{cid}/restore/", {"reason": "Investigation cleared"}, format="json"
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["data"]["campaign"]["moderationStatus"] == "live"

    # Bid restored to PENDING
    bid.refresh_from_db()
    assert bid.status == BidStatus.PENDING


@override_settings(CAMPAIGN_MODERATION_ENFORCED=True)
def test_admin_remove_campaign_declines_bids(
    brand: APIClient, creator: APIClient, campaign_payload: dict[str, Any]
) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    cid = c["id"]
    admin_client.post(f"/v1/admin/campaigns/{cid}/approve/")

    bid_resp = creator.post(
        f"/v1/campaigns/{cid}/bids/",
        {"price": 400_000, "message": "My bid", "deliveryDays": 3},
        format="json",
    )
    bid_id = bid_resp.json()["data"]["id"]

    # Remove campaign
    remove_resp = admin_client.post(
        f"/v1/admin/campaigns/{cid}/remove/", {"reason": "Terms violation"}, format="json"
    )
    assert remove_resp.status_code == 200
    assert remove_resp.json()["data"]["campaign"]["moderationStatus"] == "removed"

    # Bid is rejected
    bid = Bid.objects.get(pk=bid_id)
    assert bid.status == BidStatus.REJECTED


def test_admin_campaign_moderation_permissions(brand: APIClient, creator: APIClient, anon: APIClient) -> None:
    ts_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    sa_client, _ = _make_admin_client(role=AdminRole.SUPER_ADMIN)
    fin_client, _ = _make_admin_client(role=AdminRole.FINANCE)

    # Anon -> 401
    assert anon.get("/v1/admin/campaigns/").status_code == 401

    # Brand / Creator -> 403
    assert brand.get("/v1/admin/campaigns/").status_code == 403
    assert creator.get("/v1/admin/campaigns/").status_code == 403

    # Finance admin -> 403 (Trust & Safety required)
    assert fin_client.get("/v1/admin/campaigns/").status_code == 403

    # Trust & Safety / Super Admin -> 200
    assert ts_client.get("/v1/admin/campaigns/").status_code == 200
    assert sa_client.get("/v1/admin/campaigns/").status_code == 200
