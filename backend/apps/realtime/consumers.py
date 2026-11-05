from typing import Any

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.realtime.push import user_group

CLOSE_UNAUTHORIZED = 4401


class UserEventsConsumer(AsyncJsonWebsocketConsumer):
    """Receive-only event stream for the signed-in user: new_message, notification.

    Clients send messages through the REST API (validation, throttling and persistence stay there).
    The only accepted client frame is {"type": "ping"}, answered with {"type": "pong"}.
    """

    async def connect(self) -> None:
        user = self.scope.get("user")
        if user is None:
            await self.close(code=CLOSE_UNAUTHORIZED)
            return
        self.group = user_group(user.pk)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_json({"event": "connected"})

    async def disconnect(self, code: int) -> None:
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        if isinstance(content, dict) and content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def event_push(self, event: dict[str, Any]) -> None:
        await self.send_json(event["payload"])
