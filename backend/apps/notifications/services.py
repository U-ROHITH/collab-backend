from typing import Any
from uuid import UUID

from django.utils import timezone

from apps.core.exceptions import NotFound
from apps.notifications.models import Notification


def notify(*, user_id: Any, type: str, title: str, body: str = "", data: dict[str, Any] | None = None) -> Notification:  # noqa: A002
    """In-app notification, written in the caller's transaction (rolls back with it).

    Also pushed live over WebSocket after commit. Web push and email come later (outbox).
    """
    from apps.realtime.push import push_to_user

    n = Notification.objects.create(user_id=user_id, type=type, title=title, body=body, data=data or {})
    push_to_user(
        user_id,
        {
            "event": "notification",
            "notification": {"id": str(n.id), "type": n.type, "title": n.title, "data": n.data, "read": False},
        },
    )
    return n


def mark_read(*, user: Any, notification_id: UUID) -> Notification:
    n = Notification.objects.filter(pk=notification_id, user=user).first()
    if n is None:
        raise NotFound()
    if n.read_at is None:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at", "updated_at"])
    return n


def mark_all_read(*, user: Any) -> int:
    return Notification.objects.filter(user=user, read_at__isnull=True).update(read_at=timezone.now())
