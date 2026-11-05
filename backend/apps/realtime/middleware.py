from typing import Any
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware

from apps.realtime.tickets import consume_ticket


@database_sync_to_async
def _load_active_user(user_id: str) -> Any:
    from apps.identity.models import User

    user = User.objects.filter(pk=user_id).first()
    return user if user and user.is_active and user.role in ("brand", "creator") else None


class TicketAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> Any:
        query = parse_qs(scope.get("query_string", b"").decode())
        ticket = (query.get("ticket") or [""])[0]
        user_id = await database_sync_to_async(consume_ticket)(ticket)
        scope["user"] = await _load_active_user(user_id) if user_id else None
        return await super().__call__(scope, receive, send)
