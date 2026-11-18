import uuid
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.campaigns.models import Bid, BidStatus, Campaign, ModerationStatus
from apps.creators.models import Category, Creator
from apps.identity.models import AdminRole, Role, User
from apps.identity.services import issue_tokens
from apps.kyc.models import KycProfile, KycStatus
from apps.moderation.models import (
    BidAlert,
    BidAlertSeverity,
    BidAlertStatus,
    BidAlertType,
)
from apps.notifications.models import Notification
from apps.orders.models import Order, OrderStatus, Review, ReviewStatus, SourceType

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
    tokens = issue_tokens(admin)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens.access}")
    client.user = admin  # type: ignore[attr-defined]
    return client, admin


# --- 1. Account Suspension & Session Revocation -------------------------------------


def test_suspend_account_blocks_user_and_revokes_sessions(creator: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    creator_user = creator.user  # type: ignore[attr-defined]
    creator_obj = creator_user.creator
    creator_obj.featured = True
    creator_obj.featured_order = 1
    creator_obj.save()

    # 1. Suspend creator account
    suspend_resp = admin_client.post(
        f"/v1/admin/users/{creator_user.id}/suspend/",
        {"reason": "Terms of service violation"},
        format="json",
    )
    assert suspend_resp.status_code == 200
    res_data = suspend_resp.json()["data"]
    assert res_data["user"]["status"] == "suspended"
    assert res_data["audit"]["id"]
    assert res_data["audit"]["hash"]

    # Check creator side effect: featured is cleared
    creator_obj.refresh_from_db()
    assert creator_obj.featured is False
    assert creator_obj.featured_order == 0

    # Notification sent to user
    notif = Notification.objects.filter(user=creator_user, type="account_suspended").first()
    assert notif is not None

    # 2. Signed-in calls with existing token now receive 403 ACCOUNT_SUSPENDED
    me_resp = creator.get("/v1/creators/me/")
    assert me_resp.status_code == 403
    assert me_resp.json()["error"]["code"] == "ACCOUNT_SUSPENDED"

    # Cannot suspend already suspended user -> 409
    dup_resp = admin_client.post(
        f"/v1/admin/users/{creator_user.id}/suspend/",
        {"reason": "Again"},
        format="json",
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["error"]["code"] == "ALREADY_SUSPENDED"

    # 3. Reactivate account
    react_resp = admin_client.post(
        f"/v1/admin/users/{creator_user.id}/reactivate/",
        {"reason": "Appeal accepted"},
        format="json",
    )
    assert react_resp.status_code == 200
    assert react_resp.json()["data"]["user"]["status"] == "active"

    # Cannot reactivate active user -> 409
    dup_react = admin_client.post(f"/v1/admin/users/{creator_user.id}/reactivate/", format="json")
    assert dup_react.status_code == 409
    assert dup_react.json()["error"]["code"] == "ALREADY_ACTIVE"


def test_suspend_brand_hides_live_campaigns(brand: APIClient, campaign_payload: dict[str, Any]) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    brand_user = brand.user  # type: ignore[attr-defined]

    # Create live campaign with a pending bid
    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    camp = Campaign.objects.get(pk=c["id"])
    camp.moderation_status = ModerationStatus.LIVE
    camp.save()

    creator_user = User.objects.create_user(phone="+919876543210")
    cr = Creator.objects.create(user=creator_user, slug="bidder-c", display_name="Bidder")
    bid = Bid.objects.create(campaign=camp, creator=cr, price_paise=500_000, message="Hi", delivery_days=3)

    # Suspend brand
    suspend_resp = admin_client.post(
        f"/v1/admin/users/{brand_user.id}/suspend/",
        {"reason": "Payment fraud suspected"},
        format="json",
    )
    assert suspend_resp.status_code == 200

    # Campaign is now hidden and bid is held
    camp.refresh_from_db()
    assert camp.moderation_status == ModerationStatus.HIDDEN
    bid.refresh_from_db()
    assert bid.status == BidStatus.HELD


def test_password_reset_and_sessions(brand: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    user = brand.user  # type: ignore[attr-defined]

    # 1. Password reset flow
    pr_resp = admin_client.post(f"/v1/admin/users/{user.id}/password-reset/")
    assert pr_resp.status_code == 200
    assert pr_resp.json()["data"]["expiresInMinutes"] == 30
    assert pr_resp.json()["data"]["audit"]["id"]

    # 2. List sessions
    sess_resp = admin_client.get(f"/v1/admin/users/{user.id}/sessions/")
    assert sess_resp.status_code == 200
    sessions = sess_resp.json()["data"]
    assert len(sessions) >= 1

    # 3. Revoke sessions
    revoke_resp = admin_client.post(f"/v1/admin/users/{user.id}/sessions/revoke/")
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["data"]["revokedCount"] >= 1

    # Sessions list is now empty
    sess_after = admin_client.get(f"/v1/admin/users/{user.id}/sessions/").json()["data"]
    assert len(sess_after) == 0


# --- 2. Admin Creator Search, Detail & Curation ------------------------------------


def test_admin_creators_list_and_curate(creator: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    user = creator.user  # type: ignore[attr-defined]
    cr = user.creator

    # List creators
    list_resp = admin_client.get("/v1/admin/users/creators/")
    assert list_resp.status_code == 200
    res = list_resp.json()
    assert res["meta"]["total"] >= 1
    assert any(c["id"] == str(cr.id) for c in res["data"])

    # Detail
    detail_resp = admin_client.get(f"/v1/admin/users/creators/{cr.id}/")
    assert detail_resp.status_code == 200
    data = detail_resp.json()["data"]
    assert data["displayName"] == cr.display_name
    assert "metrics" in data
    assert "recentOrders" in data

    # Create category for tags
    Category.objects.create(name="Beauty", slug="beauty")

    # Curate creator: badges, tags, featured
    # Verified Pro requires verified KYC (which test creator has via conftest)
    curate_resp = admin_client.patch(
        f"/v1/admin/users/creators/{cr.id}/",
        {
            "badges": ["Verified Pro", "Top rated"],
            "tags": ["Beauty"],
            "featured": True,
        },
        format="json",
    )
    assert curate_resp.status_code == 200
    curated = curate_resp.json()["data"]["creator"]
    assert "Verified Pro" in curated["badges"]
    assert "Top rated" in curated["badges"]
    assert curated["tags"] == ["Beauty"]
    assert curated["featured"] is True
    assert curated["featuredOrder"] >= 1


def test_verified_pro_badge_requires_kyc(brand: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    # Creator with unverified KYC
    user = User.objects.create_user(phone="+919876599999")
    cr = Creator.objects.create(user=user, slug="unverified-c", display_name="Unverified")
    KycProfile.objects.update_or_create(user=user, defaults={"status": KycStatus.NOT_STARTED})

    resp = admin_client.patch(
        f"/v1/admin/users/creators/{cr.id}/",
        {"badges": ["Verified Pro"]},
        format="json",
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "KYC_REQUIRED"


# --- 3. Categories & Featured Ordering ----------------------------------------------


def test_categories_crud_and_merge(creator: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    cr = creator.user.creator  # type: ignore[attr-defined]

    # Create Category
    c1_resp = admin_client.post(
        "/v1/admin/users/categories/",
        {"name": "Gaming", "description": "Esports and streaming"},
        format="json",
    )
    assert c1_resp.status_code == 201
    c1 = c1_resp.json()["data"]

    c2_resp = admin_client.post(
        "/v1/admin/users/categories/",
        {"name": "Video Games", "description": "Console and PC"},
        format="json",
    )
    assert c2_resp.status_code == 201
    c2 = c2_resp.json()["data"]

    # Assign c1 to creator
    cr.categories = ["Gaming"]
    cr.tags = ["Gaming"]
    cr.save()

    # Merge Gaming into Video Games
    merge_resp = admin_client.post(
        f"/v1/admin/users/categories/{c1['id']}/merge/",
        {"targetCategoryId": c2["id"]},
        format="json",
    )
    assert merge_resp.status_code == 200
    assert merge_resp.json()["data"]["name"] == "Video Games"

    # Source category is deleted
    assert not Category.objects.filter(pk=c1["id"]).exists()

    # Creator categories updated
    cr.refresh_from_db()
    assert "Video Games" in cr.categories
    assert "Video Games" in cr.tags


def test_featured_creators_order(creator: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    cr = creator.user.creator  # type: ignore[attr-defined]

    # List featured creators
    f_resp = admin_client.get("/v1/admin/users/featured-creators/")
    assert f_resp.status_code == 200
    assert "featured" in f_resp.json()["data"]
    assert "eligible" in f_resp.json()["data"]

    # Set featured order
    ord_resp = admin_client.put(
        "/v1/admin/users/featured-creators/order/",
        {"creatorIds": [str(cr.id)]},
        format="json",
    )
    assert ord_resp.status_code == 200
    ordered = ord_resp.json()["data"]
    assert len(ordered) == 1
    assert ordered[0]["id"] == str(cr.id)
    assert ordered[0]["featured"] is True
    assert ordered[0]["featuredOrder"] == 1


# --- 4. Reviews Moderation ---------------------------------------------------------


def test_review_moderation(brand: APIClient, creator: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    brand_obj = brand.user.brand  # type: ignore[attr-defined]
    creator_obj = creator.user.creator  # type: ignore[attr-defined]

    order = Order.objects.create(
        brand=brand_obj,
        creator=creator_obj,
        source_type=SourceType.BID,
        source_id=uuid.uuid4(),
        title="Test Order",
        brief="Brief",
        deliverables="Reel",
        delivery_days=7,
        fee_config_snapshot={},
        subtotal_paise=100_000,
        platform_fee_paise=0,
        gst_paise=0,
        tds_paise=0,
        creator_payout_paise=100_000,
        brand_total_paise=100_000,
        status=OrderStatus.COMPLETED,
    )

    review = Review.objects.create(
        order=order,
        brand=brand_obj,
        creator=creator_obj,
        stars=5,
        text="Great work!",
        status=ReviewStatus.VISIBLE,
    )
    creator_obj.rating_sum = 5
    creator_obj.rating_count = 1
    creator_obj.save()

    # 1. Admin review list
    r_list = admin_client.get("/v1/admin/moderation/reviews/")
    assert r_list.status_code == 200
    assert any(r["id"] == str(review.id) for r in r_list.json()["data"])
    assert r_list.json()["meta"]["counts"]["visible"] >= 1

    # 2. Admin delete review
    del_resp = admin_client.post(
        f"/v1/admin/moderation/reviews/{review.id}/delete/",
        {"reason": "Spam or irrelevant text"},
        format="json",
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["data"]["review"]["status"] == "removed"

    # Rating recalculated
    creator_obj.refresh_from_db()
    assert creator_obj.rating_count == 0
    assert creator_obj.rating_sum == 0

    # 3. Admin keep review
    keep_resp = admin_client.post(
        f"/v1/admin/moderation/reviews/{review.id}/keep/",
        {"note": "Legitimate verified review"},
        format="json",
    )
    assert keep_resp.status_code == 200
    assert keep_resp.json()["data"]["review"]["status"] == "visible"

    creator_obj.refresh_from_db()
    assert creator_obj.rating_count == 1
    assert creator_obj.rating_sum == 5


# --- 5. Abuse Reports & Bid Alerts -------------------------------------------------


def test_abuse_report_flow(brand: APIClient, creator: APIClient) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)
    creator_user = creator.user  # type: ignore[attr-defined]
    cr = creator_user.creator

    # 1. Brand files abuse report against creator
    rep_resp = brand.post(
        "/v1/abuse-reports/",
        {
            "targetType": "creator",
            "targetId": str(cr.id),
            "reason": "Harassment in messages",
            "details": "Sent abusive messages",
        },
        format="json",
    )
    assert rep_resp.status_code == 201
    rep_id = rep_resp.json()["data"]["id"]

    # Duplicate report by same user -> 409
    dup = brand.post(
        "/v1/abuse-reports/",
        {
            "targetType": "creator",
            "targetId": str(cr.id),
            "reason": "Repeat report",
        },
        format="json",
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "ALREADY_REPORTED"

    # 2. Admin lists reports
    admin_list = admin_client.get("/v1/admin/moderation/abuse-reports/")
    assert admin_list.status_code == 200
    assert any(r["id"] == rep_id for r in admin_list.json()["data"])
    assert admin_list.json()["meta"]["counts"]["pending"] >= 1

    # 3. Admin warns target user
    warn_resp = admin_client.post(
        f"/v1/admin/moderation/abuse-reports/{rep_id}/warn/",
        {
            "reason": "Harassment violation",
            "warningMessage": "Please adhere to community guidelines or face suspension.",
        },
        format="json",
    )
    assert warn_resp.status_code == 200
    assert warn_resp.json()["data"]["report"]["actionTaken"] == "user_warned"

    # Target user received warning notification
    notif = Notification.objects.filter(user=creator_user, type="admin_warning").first()
    assert notif is not None


def test_bid_alerts_flow(brand: APIClient, campaign_payload: dict[str, Any]) -> None:
    admin_client, _ = _make_admin_client(role=AdminRole.TRUST_SAFETY)

    c = brand.post("/v1/campaigns/", campaign_payload, format="json").json()["data"]
    camp = Campaign.objects.get(pk=c["id"])

    # Create 2 creators and bids
    u1 = User.objects.create_user(phone="+919876500001")
    cr1 = Creator.objects.create(user=u1, slug="c-1", display_name="C 1")
    b1 = Bid.objects.create(campaign=camp, creator=cr1, price_paise=10_000, message="Low", delivery_days=2)

    # Create bid alert
    alert = BidAlert.objects.create(
        campaign=camp,
        alert_type=BidAlertType.SUSPICIOUS_LOW_BID,
        severity=BidAlertSeverity.HIGH,
        status=BidAlertStatus.PENDING,
        details={"minBudget": camp.budget_min_paise, "bidPrice": 10_000},
    )
    alert.bids.add(b1)

    # 1. Admin list alerts
    alerts_list = admin_client.get("/v1/admin/moderation/bid-alerts/")
    assert alerts_list.status_code == 200
    assert any(a["id"] == str(alert.id) for a in alerts_list.json()["data"])

    # 2. Freeze bids
    freeze_resp = admin_client.post(f"/v1/admin/moderation/bid-alerts/{alert.id}/freeze-bids/")
    assert freeze_resp.status_code == 200
    assert freeze_resp.json()["data"]["alert"]["status"] == "frozen"

    b1.refresh_from_db()
    assert b1.status == BidStatus.HELD

    # 3. Dismiss alert
    dismiss_resp = admin_client.post(
        f"/v1/admin/moderation/bid-alerts/{alert.id}/dismiss/",
        {"reason": "False positive"},
        format="json",
    )
    assert dismiss_resp.status_code == 200
    assert dismiss_resp.json()["data"]["alert"]["status"] == "dismissed"
