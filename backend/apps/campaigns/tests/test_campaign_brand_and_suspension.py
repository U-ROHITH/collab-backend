from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.brands.models import Brand
from apps.campaigns.models import Campaign, CampaignStatus, ModerationStatus
from apps.campaigns.services import public_open_campaigns
from apps.creators.models import ApprovalStatus, Creator
from apps.identity.models import Role, User, UserStatus
from apps.identity.services import issue_tokens

pytestmark = pytest.mark.django_db


@pytest.fixture
def brand_owner() -> User:
    user = User.objects.create(
        email="brand_owner@test.com",
        phone="+919876543210",
        role=Role.BRAND,
        status=UserStatus.ACTIVE,
    )
    Brand.objects.create(
        owner=user,
        name="Test Brand Co",
        logo_url="https://test.com/logo.png",
    )
    return user


@pytest.fixture
def creator_user() -> User:
    user = User.objects.create(
        email="creator@test.com",
        phone="+919876543211",
        role=Role.CREATOR,
        status=UserStatus.ACTIVE,
    )
    Creator.objects.create(
        user=user,
        slug="testcreator",
        display_name="Test Creator",
        approval_status=ApprovalStatus.APPROVED,
    )
    return user


def test_campaign_brand_exposed_to_creator_and_public(brand_owner: User, creator_user: User) -> None:
    brand = brand_owner.brand
    campaign = Campaign.objects.create(
        brand=brand,
        title="Spring Collab",
        brief="Promote spring collection",
        platform="instagram",
        deliverables="1 Reel",
        budget_min_paise=500_000,
        budget_max_paise=1_000_000,
        closes_at=timezone.now() + timedelta(days=7),
        status=CampaignStatus.OPEN,
        moderation_status=ModerationStatus.LIVE,
    )

    # 1. Creator authenticated request
    creator_client = APIClient()
    creator_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(creator_user).access}")
    resp = creator_client.get(f"/v1/campaigns/{campaign.id}/")
    assert resp.status_code == 200
    brand_data = resp.json()["data"]["brand"]
    assert brand_data is not None
    assert brand_data["id"] == str(brand.id)
    assert brand_data["userId"] == str(brand_owner.id)
    assert brand_data["name"] == "Test Brand Co"
    assert brand_data["logoUrl"] == "https://test.com/logo.png"
    assert brand_data["accountStatus"] == "active"

    # 2. Public / Guest request
    guest_client = APIClient()
    resp = guest_client.get(f"/v1/campaigns/{campaign.id}/")
    assert resp.status_code == 200
    brand_data = resp.json()["data"]["brand"]
    assert brand_data is not None
    assert brand_data["id"] == str(brand.id)
    assert brand_data["name"] == "Test Brand Co"
    assert brand_data["accountStatus"] == "active"


def test_suspended_brand_campaigns_are_hidden(brand_owner: User, creator_user: User) -> None:
    brand = brand_owner.brand
    campaign = Campaign.objects.create(
        brand=brand,
        title="Summer Collab",
        brief="Promote summer collection",
        platform="instagram",
        deliverables="1 Reel",
        budget_min_paise=500_000,
        budget_max_paise=1_000_000,
        closes_at=timezone.now() + timedelta(days=7),
        status=CampaignStatus.OPEN,
        moderation_status=ModerationStatus.LIVE,
    )

    # Initially visible
    assert public_open_campaigns().filter(id=campaign.id).exists()

    # Suspend the brand owner
    brand_owner.status = UserStatus.SUSPENDED
    brand_owner.save(update_fields=["status"])

    # Hidden from public_open_campaigns
    assert not public_open_campaigns().filter(id=campaign.id).exists()

    # Creator request gets 404
    creator_client = APIClient()
    creator_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(creator_user).access}")
    resp = creator_client.get(f"/v1/campaigns/{campaign.id}/")
    assert resp.status_code == 404

    # Public request gets 404
    guest_client = APIClient()
    resp = guest_client.get(f"/v1/campaigns/{campaign.id}/")
    assert resp.status_code == 404


def test_creator_can_rebid_after_withdrawing_bid(brand_owner: User, creator_user: User) -> None:
    from apps.kyc.models import KycProfile, KycStatus

    KycProfile.objects.create(
        user=creator_user,
        status=KycStatus.VERIFIED,
    )

    brand = brand_owner.brand
    campaign = Campaign.objects.create(
        brand=brand,
        title="Collab Brief",
        brief="Promote our product",
        platform="instagram",
        deliverables="1 Reel",
        budget_min_paise=500_000,
        budget_max_paise=1_000_000,
        closes_at=timezone.now() + timedelta(days=7),
        status=CampaignStatus.OPEN,
        moderation_status=ModerationStatus.LIVE,
    )

    creator_client = APIClient()
    creator_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(creator_user).access}")

    # 1. Submit initial bid -> 201 Created
    bid_payload = {"price": 600_000, "message": "My first bid proposal", "deliveryDays": 5}
    resp1 = creator_client.post(f"/v1/campaigns/{campaign.id}/bids/", bid_payload, format="json")
    assert resp1.status_code == 201
    bid_data1 = resp1.json()["data"]
    bid_id = bid_data1["id"]
    assert bid_data1["status"] == "pending"
    assert bid_data1["price"]["amount"] == 600_000

    # 2. Duplicate bid while pending -> 409 BID_EXISTS
    resp_dup = creator_client.post(f"/v1/campaigns/{campaign.id}/bids/", bid_payload, format="json")
    assert resp_dup.status_code == 409
    assert resp_dup.json()["error"]["code"] == "BID_EXISTS"

    # 3. Withdraw bid -> 200 OK, status='withdrawn'
    resp_withdraw = creator_client.post(f"/v1/campaigns/{campaign.id}/bids/{bid_id}/withdraw/")
    assert resp_withdraw.status_code == 200
    assert resp_withdraw.json()["data"]["status"] == "withdrawn"

    # 4. Re-bid after withdrawal -> 201 Created, status resets to 'pending' with new price/message
    new_bid_payload = {"price": 750_000, "message": "My revised new bid proposal", "deliveryDays": 3}
    resp2 = creator_client.post(f"/v1/campaigns/{campaign.id}/bids/", new_bid_payload, format="json")
    assert resp2.status_code == 201
    bid_data2 = resp2.json()["data"]
    assert bid_data2["id"] == bid_id
    assert bid_data2["status"] == "pending"
    assert bid_data2["price"]["amount"] == 750_000
    assert bid_data2["message"] == "My revised new bid proposal"
    assert bid_data2["deliveryDays"] == 3

    # 5. Duplicate bid while pending again -> 409 BID_EXISTS
    resp_dup2 = creator_client.post(f"/v1/campaigns/{campaign.id}/bids/", new_bid_payload, format="json")
    assert resp_dup2.status_code == 409
    assert resp_dup2.json()["error"]["code"] == "BID_EXISTS"

