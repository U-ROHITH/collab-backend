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
