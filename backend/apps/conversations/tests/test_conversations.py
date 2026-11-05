import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.conversations.models import Conversation, Message
from apps.offers.models import Offer, OfferStatus
from apps.orders.models import Order, OrderStatus, SourceType
from tests.fixtures import USAGE_RIGHTS

pytestmark = pytest.mark.django_db


def test_brand_cannot_initiate_conversation_without_active_link(brand: APIClient, creator: Any) -> None:
    creator_id = str(creator.user.creator.id)
    resp = brand.post(
        "/v1/conversations/",
        {"creatorId": creator_id, "content": "Hi there!"},
        format="json",
    )
    assert resp.status_code == 422
    data = resp.json()
    assert data["success"] is False
    assert data["error"]["code"] == "CONVERSATION_NOT_ALLOWED"


def test_brand_can_initiate_conversation_with_active_campaign_bid(
    brand: APIClient, creator: Any, campaign_payload: dict[str, Any]
) -> None:
    # 1. Brand creates campaign
    camp_resp = brand.post("/v1/campaigns/", campaign_payload, format="json")
    assert camp_resp.status_code == 201
    campaign_id = camp_resp.json()["data"]["id"]

    # 2. Creator bids on campaign
    bid_resp = creator.post(
        f"/v1/campaigns/{campaign_id}/bids/",
        {
            "price": 1000000,
            "message": "I would love to collaborate on this campaign!",
            "deliveryDays": 5,
        },
        format="json",
    )
    assert bid_resp.status_code == 201

    # 3. Brand initiates conversation with creator
    creator_id = str(creator.user.creator.id)
    resp = brand.post(
        "/v1/conversations/",
        {"creatorId": creator_id, "content": "Hello, loved your proposal!"},
        format="json",
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["id"] is not None
    assert data["brand"]["name"] == brand.user.brand.name
    assert data["creator"]["displayName"] == creator.user.creator.display_name
    assert data["lastMessage"]["content"] == "Hello, loved your proposal!"
    assert data["lastMessage"]["senderRole"] == "brand"

    # Verify message in DB
    assert Message.objects.filter(conversation_id=data["id"]).count() == 1


def test_brand_can_initiate_conversation_with_active_offer(brand: APIClient, creator: Any) -> None:
    # Direct proposal/offer from brand to creator
    Offer.objects.create(
        brand=brand.user.brand,
        creator=creator.user.creator,
        deliverable="1 Reel",
        budget_paise=1_000_000,
        deadline=timezone.now() + timedelta(days=7),
        message="Direct partnership offer",
        usage_rights=USAGE_RIGHTS,
        status=OfferStatus.PENDING,
    )

    creator_id = str(creator.user.creator.id)
    resp = brand.post(
        "/v1/conversations/",
        {"creatorId": creator_id, "content": "Sent you a partnership offer!"},
        format="json",
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["brand"]["id"] == str(brand.user.brand.id)
    assert data["creator"]["id"] == creator_id


def test_brand_can_initiate_conversation_with_active_order(brand: APIClient, creator: Any) -> None:
    Order.objects.create(
        brand=brand.user.brand,
        creator=creator.user.creator,
        source_type=SourceType.OFFER,
        source_id=uuid.uuid4(),
        status=OrderStatus.FUNDED,
        title="1 Instagram Reel",
        deliverables="1 Reel",
        delivery_days=5,
        fee_config_snapshot={},
        subtotal_paise=100_000,
        platform_fee_paise=10_000,
        gst_paise=1_800,
        tds_paise=1_000,
        creator_payout_paise=90_000,
        brand_total_paise=102_800,
    )

    creator_id = str(creator.user.creator.id)
    resp = brand.post(
        "/v1/conversations/",
        {"creatorId": creator_id},
        format="json",
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["creator"]["id"] == creator_id


def test_brand_cannot_send_message_without_active_link(brand: APIClient, creator: Any) -> None:
    # Create conversation directly (e.g. from previous completed or cancelled state)
    conv = Conversation.objects.create(
        brand=brand.user.brand,
        creator=creator.user.creator,
    )

    # Attempt to send message without any active order/bid/offer
    resp = brand.post(
        f"/v1/conversations/{conv.id}/messages/",
        {"content": "Hey, are you free for a new gig?"},
        format="json",
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CONVERSATION_NOT_ALLOWED"


def test_conversation_messages_flow_and_read_status(brand: APIClient, creator: Any) -> None:
    # Establish link via Offer
    Offer.objects.create(
        brand=brand.user.brand,
        creator=creator.user.creator,
        deliverable="1 Reel",
        budget_paise=1_000_000,
        deadline=timezone.now() + timedelta(days=7),
        message="Direct offer",
        usage_rights=USAGE_RIGHTS,
        status=OfferStatus.PENDING,
    )

    # 1. Brand initiates conversation and sends message
    init_resp = brand.post(
        "/v1/conversations/",
        {"creatorId": str(creator.user.creator.id), "content": "Hi Creator, welcome!"},
        format="json",
    )
    assert init_resp.status_code == 201
    conv_id = init_resp.json()["data"]["id"]

    # 2. Creator lists conversations -> unreadCount should be 1
    creator_convs = creator.get("/v1/conversations/")
    assert creator_convs.status_code == 200
    conv_list = creator_convs.json()["data"]
    assert len(conv_list) == 1
    assert conv_list[0]["id"] == conv_id
    assert conv_list[0]["unreadCount"] == 1
    assert conv_list[0]["lastMessage"]["content"] == "Hi Creator, welcome!"

    # 3. Creator retrieves messages -> marks messages as read
    msg_resp = creator.get(f"/v1/conversations/{conv_id}/messages/")
    assert msg_resp.status_code == 200
    messages = msg_resp.json()["data"]
    assert len(messages) == 1
    assert messages[0]["content"] == "Hi Creator, welcome!"
    assert messages[0]["isRead"] is True

    # 4. Creator lists conversations again -> unreadCount should be 0
    creator_convs_again = creator.get("/v1/conversations/")
    assert creator_convs_again.json()["data"][0]["unreadCount"] == 0

    # 5. Creator replies
    reply_resp = creator.post(
        f"/v1/conversations/{conv_id}/messages/",
        {"content": "Thanks! Looking forward to it."},
        format="json",
    )
    assert reply_resp.status_code == 201
    reply_data = reply_resp.json()["data"]
    assert reply_data["content"] == "Thanks! Looking forward to it."
    assert reply_data["senderRole"] == "creator"
    assert reply_data["isRead"] is False

    # 6. Brand gets messages -> sees 2 messages, second one from creator
    brand_msgs = brand.get(f"/v1/conversations/{conv_id}/messages/")
    assert brand_msgs.status_code == 200
    b_messages = brand_msgs.json()["data"]
    assert len(b_messages) == 2
    assert b_messages[1]["content"] == "Thanks! Looking forward to it."
    assert b_messages[1]["isRead"] is True


def test_non_participant_access_denied(brand: APIClient, creator: Any, make_brand: Any) -> None:
    # Setup conversation
    Offer.objects.create(
        brand=brand.user.brand,
        creator=creator.user.creator,
        deliverable="1 Reel",
        budget_paise=1_000_000,
        deadline=timezone.now() + timedelta(days=7),
        message="Direct offer",
        usage_rights=USAGE_RIGHTS,
        status=OfferStatus.PENDING,
    )
    init_resp = brand.post(
        "/v1/conversations/",
        {"creatorId": str(creator.user.creator.id), "content": "Hello"},
        format="json",
    )
    conv_id = init_resp.json()["data"]["id"]

    other_brand = make_brand(name="Other Unrelated Brand")

    # Non-participant GET messages -> 404
    get_resp = other_brand.get(f"/v1/conversations/{conv_id}/messages/")
    assert get_resp.status_code == 404

    # Non-participant POST messages -> 404
    post_resp = other_brand.post(
        f"/v1/conversations/{conv_id}/messages/",
        {"content": "I am intruding"},
        format="json",
    )
    assert post_resp.status_code == 404

    # Other brand's conversation list does not contain this conversation
    assert other_brand.get("/v1/conversations/").json()["data"] == []


def test_unauthenticated_requests_denied(anon: APIClient) -> None:
    fake_id = uuid.uuid4()
    assert anon.get("/v1/conversations/").status_code == 401
    assert anon.post("/v1/conversations/", {"creatorId": str(fake_id)}).status_code == 401
    assert anon.get(f"/v1/conversations/{fake_id}/messages/").status_code == 401
    assert anon.post(f"/v1/conversations/{fake_id}/messages/", {"content": "hi"}).status_code == 401
