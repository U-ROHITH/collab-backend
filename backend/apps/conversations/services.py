from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.brands.models import Brand
from apps.campaigns.models import Bid, BidStatus
from apps.conversations.models import Conversation, Message
from apps.core.exceptions import ConversationNotAllowed, NotFound
from apps.creators.models import Creator
from apps.identity.models import Role, User
from apps.notifications.services import notify
from apps.offers.models import Offer, OfferStatus
from apps.orders.models import Order, OrderStatus


def can_brand_message_creator(brand: Brand, creator: Creator) -> bool:
    """Check if an active order, bid, proposal/offer, or accepted application links the brand and creator."""
    # 1. Active order linking them (excluding cancelled or refunded)
    if (
        Order.objects.filter(brand=brand, creator=creator)
        .exclude(status__in=[OrderStatus.CANCELLED, OrderStatus.REFUNDED])
        .exists()
    ):
        return True

    # 2. Active bid or accepted application on a campaign created by the brand
    if Bid.objects.filter(
        campaign__brand=brand,
        creator=creator,
        status__in=[BidStatus.PENDING, BidStatus.SELECTED],
    ).exists():
        return True

    # 3. Active direct proposal / offer between them
    if Offer.objects.filter(
        brand=brand,
        creator=creator,
        status__in=[OfferStatus.PENDING, OfferStatus.ACCEPTED],
    ).exists():
        return True

    return False


def assert_can_message(*, sender_user: User, brand: Brand, creator: Creator) -> None:
    """Enforce the brand-first messaging rule.

    Rejects with ConversationNotAllowed (HTTP 422) if no active order, bid, or proposal exists.
    """
    if not can_brand_message_creator(brand, creator):
        raise ConversationNotAllowed("A brand cannot message a creator without an active order, bid, or proposal.")


@transaction.atomic
def get_or_create_conversation(
    *, user: User, target_creator_id: UUID | None = None, target_brand_id: UUID | None = None
) -> tuple[Conversation, bool]:
    """Retrieve or initiate a conversation adhering to the brand-first messaging rule."""
    if user.role == Role.BRAND:
        brand = getattr(user, "brand", None)
        if brand is None:
            raise NotFound("Brand profile not found.")
        if not target_creator_id:
            raise NotFound("Recipient creator not specified.")
        try:
            creator = Creator.objects.get(pk=target_creator_id)
        except Creator.DoesNotExist:
            raise NotFound("Creator not found.") from None

    elif user.role == Role.CREATOR:
        creator = getattr(user, "creator", None)
        if creator is None:
            raise NotFound("Creator profile not found.")
        if not target_brand_id:
            raise NotFound("Recipient brand not specified.")
        try:
            brand = Brand.objects.get(pk=target_brand_id)
        except Brand.DoesNotExist:
            raise NotFound("Brand not found.") from None
    else:
        raise NotFound("User role does not allow conversations.")

    # Brand-First Messaging Rule validation
    assert_can_message(sender_user=user, brand=brand, creator=creator)

    conversation, created = Conversation.objects.get_or_create(brand=brand, creator=creator)
    return conversation, created


@transaction.atomic
def send_message(*, conversation: Conversation, sender_user: User, content: str) -> Message:
    """Send and persist a message within a conversation."""
    content = content.strip()
    if not content:
        raise ConversationNotAllowed("Message content cannot be blank.")

    is_brand_user = hasattr(sender_user, "brand") and conversation.brand_id == sender_user.brand.id
    is_creator_user = hasattr(sender_user, "creator") and conversation.creator_id == sender_user.creator.id

    if not is_brand_user and not is_creator_user:
        raise NotFound("Conversation not found.")

    # Enforce brand-first messaging check
    assert_can_message(sender_user=sender_user, brand=conversation.brand, creator=conversation.creator)

    now = timezone.now()
    msg = Message.objects.create(
        conversation=conversation,
        sender=sender_user,
        sender_role=sender_user.role,
        content=content,
    )
    conversation.last_message_at = now
    conversation.save(update_fields=["last_message_at", "updated_at"])

    # Disptach in-app notification to the recipient
    recipient_user = conversation.creator.user if is_brand_user else conversation.brand.owner
    sender_name = sender_user.full_name or ("Brand" if is_brand_user else "Creator")
    notify(
        user_id=recipient_user.id,
        type="new_message",
        title=f"New message from {sender_name}",
        body=content[:100],
        data={"conversationId": str(conversation.id), "messageId": str(msg.id)},
    )

    return msg


def mark_messages_as_read(*, conversation: Conversation, user: Any) -> int:
    """Mark unread messages from the other participant as read."""
    return (
        conversation.messages.filter(is_read=False)
        .exclude(sender=user)
        .update(is_read=True, read_at=timezone.now(), updated_at=timezone.now())
    )
