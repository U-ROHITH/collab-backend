import pytest

from apps.brands.models import Brand
from apps.campaigns.models import Bid, Campaign
from apps.creators.models import Creator, CreatorPackage
from apps.identity.models import Role, User
from apps.identity.services import delete_user_completely

pytestmark = pytest.mark.django_db


def test_delete_user_completely_creator_with_bids_and_packages():
    # 1. Setup creator user with a package and bid
    user = User.objects.create_user(email="creator_to_delete@example.com", role=Role.CREATOR)
    creator = Creator.objects.create(user=user, display_name="Creator To Delete", slug="creator-to-delete")
    package = CreatorPackage.objects.create(
        creator=creator,
        title="Test Package",
        platform="instagram",
        deliverables="1 Reel",
        price_paise=100000,
        delivery_days=3,
    )
    
    brand_owner = User.objects.create_user(email="brand_owner@example.com", role=Role.BRAND)
    brand = Brand.objects.create(owner=brand_owner, name="Test Brand")
    campaign = Campaign.objects.create(
        brand=brand,
        title="Test Campaign",
        deliverables="1 Reel",
        budget_min_paise=100000,
        budget_max_paise=500000,
        platform="instagram",
        closes_at="2026-12-31",
    )
    bid = Bid.objects.create(
        campaign=campaign,
        creator=creator,
        price_paise=150000,
        delivery_days=2,
    )

    # 2. Execute complete deletion
    delete_user_completely(user=user)

    # 3. Assert everything is wiped
    assert not User.objects.filter(pk=user.pk).exists()
    assert not Creator.objects.filter(pk=creator.pk).exists()
    assert not CreatorPackage.objects.filter(pk=package.pk).exists()
    assert not Bid.objects.filter(pk=bid.pk).exists()

    # Brand and campaign should still exist
    assert Brand.objects.filter(pk=brand.pk).exists()
    assert Campaign.objects.filter(pk=campaign.pk).exists()


def test_delete_user_completely_brand_with_campaigns():
    brand_owner = User.objects.create_user(email="brand_delete@example.com", role=Role.BRAND)
    brand = Brand.objects.create(owner=brand_owner, name="Brand To Delete")
    campaign = Campaign.objects.create(
        brand=brand,
        title="Brand Campaign",
        deliverables="1 Reel",
        budget_min_paise=100000,
        budget_max_paise=500000,
        platform="instagram",
        closes_at="2026-12-31",
    )

    delete_user_completely(user=brand_owner)

    assert not User.objects.filter(pk=brand_owner.pk).exists()
    assert not Brand.objects.filter(pk=brand.pk).exists()
    assert not Campaign.objects.filter(pk=campaign.pk).exists()
