from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.brands.models import Brand
from apps.core.exceptions import Conflict, DomainError, NotFound
from apps.creators.models import Creator
from apps.identity.models import Role, User
from apps.messaging.models import Conversation, Message
from apps.messaging.selectors import conversations_for, my_read_field
from apps.notifications.models import Notification
from apps.notifications.services import notify
from apps.realtime.push import push_to_user


class ConversationNotAllowed(DomainError):
    code = "CONVERSATION_NOT_ALLOWED"
    message = "You can message this account once there's a bid, offer or order between you."


def has_relationship(brand: Brand, creator: Creator) -> bool:
    """Messaging opens only after real business contact (anti-spam, anti off-platform poaching)."""
    from apps.campaigns.models import Bid
    from apps.offers.models import Offer
    from apps.orders.models import Order

    return (
        Order.objects.filter(brand=brand, creator=creator).exists()
        or Offer.objects.filter(brand=brand, creator=creator).exists()
        or Bid.objects.filter(campaign__brand=brand, creator=creator).exists()
    )


def open_conversation(*, user: User, counterpart_id: UUID) -> Conversation:
    if user.role == Role.BRAND:
        brand = user.brand
        creator = Creator.objects.filter(pk=counterpart_id).first()
    elif user.role == Role.CREATOR:
        creator = user.creator
        brand = Brand.objects.filter(pk=counterpart_id).first()
    else:
        raise NotFound()
    if brand is None or creator is None or creator.user_id is None:
        raise NotFound("Account not found.")
    if not has_relationship(brand, creator):
        raise ConversationNotAllowed()
    existing = Conversation.objects.filter(brand=brand, creator=creator).first()
    if existing:
        return existing
    try:
        with transaction.atomic():
            return Conversation.objects.create(brand=brand, creator=creator)
    except IntegrityError:  # opened concurrently by the other side
        return Conversation.objects.get(brand=brand, creator=creator)


def _recipient_id(conversation: Conversation, sender: User) -> Any:
    return conversation.creator.user_id if sender.role == Role.BRAND else conversation.brand.owner_id


def send_message(*, user: User, conversation_id: UUID, text: str, attachment_upload_id: UUID | None) -> Message:
    from apps.uploads.models import UploadKind
    from apps.uploads.services import claim_ready_upload

    with transaction.atomic():
        conversation = (
            conversations_for(user)
            .select_for_update(of=("self",))
            .select_related("brand", "creator")
            .filter(pk=conversation_id)
            .first()
        )
        if conversation is None:
            raise NotFound()
        attachment = None
        if attachment_upload_id:
            attachment = claim_ready_upload(user=user, upload_id=attachment_upload_id, kind=UploadKind.CHAT_ATTACHMENT)
            if Message.objects.filter(attachment=attachment).exists():
                raise Conflict("This file was already sent.", code="ATTACHMENT_IN_USE")
        message = Message.objects.create(conversation=conversation, sender=user, body=text, attachment=attachment)
        conversation.last_message_at = message.created_at
        setattr(conversation, my_read_field(user), message.created_at)  # sender has read their own message
        conversation.save(update_fields=["last_message_at", my_read_field(user), "updated_at"])

        recipient_id = _recipient_id(conversation, user)
        sender_name = conversation.brand.name if user.role == Role.BRAND else conversation.creator.display_name
        # One unread "new_message" notification per conversation, not one per message.
        already = Notification.objects.filter(
            user_id=recipient_id,
            type="new_message",
            read_at__isnull=True,
            data__conversationId=str(conversation.pk),
        ).exists()
        if not already:
            notify(
                user_id=recipient_id,
                type="new_message",
                title=f"New message from {sender_name}",
                data={"conversationId": str(conversation.pk)},
            )

        from apps.messaging.serializers import MessageOutput

        for uid in (recipient_id, user.pk):
            payload = MessageOutput(message, context={"user_id": uid}).data
            push_to_user(uid, {"event": "new_message", "conversationId": str(conversation.pk), "message": payload})
    return message


def mark_read(*, user: User, conversation_id: UUID) -> Conversation:
    conversation = conversations_for(user).filter(pk=conversation_id).first()
    if conversation is None:
        raise NotFound()
    field = my_read_field(user)
    setattr(conversation, field, timezone.now())
    conversation.save(update_fields=[field, "updated_at"])
    Notification.objects.filter(
        user=user, type="new_message", read_at__isnull=True, data__conversationId=str(conversation.pk)
    ).update(read_at=timezone.now())
    return conversation
