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


# --- realistic accounts ---------------------------------------------------------------------------


def _with_kyc_and_chat(brand_client, creator_client) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from django.utils import timezone

    from tests.fixtures import USAGE_RIGHTS

    offer = {
        "creatorId": str(creator_client.user.creator.id),
        "deliverable": "1 Reel",
        "budget": 500_000,
        "deadline": (timezone.now() + timedelta(days=5)).isoformat(),
        "message": "hi",
        "usageRights": USAGE_RIGHTS,
    }
    assert brand_client.post("/v1/offers/", offer, format="json").status_code == 201
    cid = brand_client.post(
        "/v1/conversations/", {"creatorId": str(creator_client.user.creator.id)}, format="json"
    ).json()["data"]["id"]
    assert brand_client.post(f"/v1/conversations/{cid}/messages/", {"text": "hello"}, format="json").status_code == 201
    creator_client.post(f"/v1/conversations/{cid}/messages/", {"text": "hi back"}, format="json")


def test_creator_with_kyc_chat_and_offers_is_fully_deleted(make_brand, make_creator) -> None:  # type: ignore[no-untyped-def]
    from apps.kyc.models import KycProfile
    from apps.messaging.models import Conversation

    brand, creator = make_brand("Keep Co"), make_creator("Delete Me")
    _with_kyc_and_chat(brand, creator)
    user = creator.user
    assert KycProfile.objects.filter(user=user).exists()

    delete_user_completely(user=user)

    assert not User.objects.filter(pk=user.pk).exists()
    assert not KycProfile.objects.filter(user_id=user.pk).exists()
    assert not Conversation.objects.filter(creator__user_id=user.pk).exists()
    assert Brand.objects.filter(owner=brand.user).exists()  # the other side is untouched


def test_brand_with_chat_is_fully_deleted(make_brand, make_creator) -> None:  # type: ignore[no-untyped-def]
    brand, creator = make_brand("Delete Co"), make_creator("Keep Me")
    _with_kyc_and_chat(brand, creator)
    delete_user_completely(user=brand.user)
    assert not User.objects.filter(pk=brand.user.pk).exists()
    assert Creator.objects.filter(user=creator.user).exists()


def test_user_with_orders_is_refused_and_nothing_is_deleted(brand, creator, package) -> None:  # type: ignore[no-untyped-def]
    from apps.core.exceptions import Conflict
    from apps.identity.models import AuditLog
    from apps.orders import services as order_services
    from apps.orders.models import Order
    from tests.fixtures import USAGE_RIGHTS

    order_services.create_order(
        actor=brand.user,
        brand=brand.user.brand,
        creator=creator.user.creator,
        source_type="package",
        source_id=package.pk,
        title="t",
        deliverables="d",
        brief="b",
        subtotal_paise=package.price_paise,
        delivery_days=3,
        usage_rights=USAGE_RIGHTS,
    )
    audit_rows = AuditLog.objects.count()
    with pytest.raises(Conflict) as exc:
        delete_user_completely(user=creator.user)
    assert exc.value.code == "USER_HAS_FINANCIAL_RECORDS"
    assert User.objects.filter(pk=creator.user.pk).exists() and Order.objects.count() == 1
    assert AuditLog.objects.count() == audit_rows
