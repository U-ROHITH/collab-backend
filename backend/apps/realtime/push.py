"""Server -> browser push over WebSocket (Django Channels groups, one group per user).

Pushes are best-effort and only sent after the DB transaction commits, so clients never see an
event for data that was rolled back. Clients should still refetch on reconnect.
"""

import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

logger = logging.getLogger(__name__)


def user_group(user_id: Any) -> str:
    return f"user_{user_id}".replace("-", "")


def _send(user_id: Any, payload: dict[str, Any]) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(user_group(user_id), {"type": "event.push", "payload": payload})
    except Exception:  # noqa: BLE001 - a push failure must never break the request
        logger.warning("Realtime push failed", exc_info=True)


def push_to_user(user_id: Any, payload: dict[str, Any]) -> None:
    if user_id is None:
        return
    data = dict(payload)
    transaction.on_commit(lambda: _send(user_id, data))
