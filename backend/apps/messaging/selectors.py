from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery

from apps.identity.models import Role, User
from apps.messaging.models import Conversation, Message


def conversations_for(user: User) -> QuerySet[Conversation]:
    """Every conversation query starts here: a user only sees threads they are part of."""
    if user.role == Role.BRAND:
        return Conversation.objects.filter(brand__owner=user)
    if user.role == Role.CREATOR:
        return Conversation.objects.filter(creator__user=user)
    return Conversation.objects.none()


def my_read_field(user: User) -> str:
    return "brand_last_read_at" if user.role == Role.BRAND else "creator_last_read_at"


def conversations_with_summary(user: User) -> QuerySet[Conversation]:
    """List view: last message preview + my unread count, in one query (no N+1)."""
    read_field = my_read_field(user)
    latest = Message.objects.filter(conversation=OuterRef("pk")).order_by("-created_at", "-id")
    from_other = ~Q(messages__sender=user)
    after_my_read = Q(**{f"{read_field}__isnull": True}) | Q(messages__created_at__gt=F(read_field))
    return (
        conversations_for(user)
        .select_related("brand", "creator")
        .annotate(
            last_body=Subquery(latest.values("body")[:1]),
            last_sender_id=Subquery(latest.values("sender_id")[:1]),
            last_attachment_id=Subquery(latest.values("attachment_id")[:1]),
            unread=Count("messages", filter=from_other & after_my_read),
        )
    )
