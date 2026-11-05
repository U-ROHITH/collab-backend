"""Single-use WebSocket tickets.

Browsers can't set an Authorization header on a WebSocket, and putting a JWT in the URL leaks it
into proxy/server logs. Instead the client trades its JWT for a random ticket (valid 30s, usable
once) and connects with ?ticket=... . Tickets live in the shared cache (Redis in production).
"""

import secrets
from typing import Any

from django.core.cache import cache

TICKET_TTL_SECONDS = 30
_PREFIX = "ws_ticket:"


def issue_ticket(user_id: Any) -> str:
    ticket = secrets.token_urlsafe(32)
    cache.set(_PREFIX + ticket, str(user_id), TICKET_TTL_SECONDS)
    return ticket


def consume_ticket(ticket: str) -> str | None:
    if not ticket or len(ticket) > 100:
        return None
    key = _PREFIX + ticket
    user_id = cache.get(key)
    # delete() returns True only for the caller that actually removed it -> single use even under races.
    if user_id is None or not cache.delete(key):
        return None
    return user_id
