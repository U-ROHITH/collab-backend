from typing import Any

from rest_framework import serializers

from apps.core.dates import ist_date, ist_time
from apps.core.parties import brand_public, creator_public
from apps.core.serializers import StrictSerializer
from apps.identity.models import Role
from apps.messaging.models import Conversation, Message


def _attachment(upload: Any) -> dict[str, Any] | None:
    if upload is None:
        return None
    return {"uploadId": str(upload.id), "contentType": upload.content_type, "size": upload.size_bytes}


class MessageOutput(serializers.ModelSerializer):
    fromMe = serializers.SerializerMethodField()  # noqa: N815
    text = serializers.CharField(source="body")
    attachment = serializers.SerializerMethodField()
    sentAt = serializers.DateTimeField(source="created_at")  # noqa: N815  full timestamp (UTC)
    sentDate = serializers.SerializerMethodField()  # noqa: N815  "YYYY-MM-DD" in IST, for day headers
    sentTime = serializers.SerializerMethodField()  # noqa: N815  "HH:MM" in IST (24h)
    senderId = serializers.UUIDField(source="sender_id")  # noqa: N815

    class Meta:
        model = Message
        fields = ("id", "senderId", "fromMe", "text", "attachment", "sentAt", "sentDate", "sentTime")

    def get_sentDate(self, obj: Message) -> str:  # noqa: N802
        return ist_date(obj.created_at).isoformat()

    def get_sentTime(self, obj: Message) -> str:  # noqa: N802
        return ist_time(obj.created_at)

    def get_fromMe(self, obj: Message) -> bool:  # noqa: N802
        uid = self.context.get("user_id")
        if uid is None and (request := self.context.get("request")):
            uid = request.user.pk
        return str(obj.sender_id) == str(uid)

    def get_attachment(self, obj: Message) -> dict[str, Any] | None:
        return _attachment(obj.attachment)


class ConversationOutput(serializers.ModelSerializer):
    withParty = serializers.SerializerMethodField()  # noqa: N815
    lastMessage = serializers.SerializerMethodField()  # noqa: N815
    unread = serializers.SerializerMethodField()
    updatedAt = serializers.SerializerMethodField()  # noqa: N815

    class Meta:
        model = Conversation
        fields = ("id", "withParty", "lastMessage", "unread", "updatedAt")

    def _me(self) -> Any:
        return self.context["request"].user

    def get_withParty(self, obj: Conversation) -> dict[str, Any]:  # noqa: N802
        if self._me().role == Role.BRAND:
            c = obj.creator
            return {
                "type": "creator",
                "id": str(c.id),
                "name": c.display_name,
                "image": c.avatar_url or None,
                "profile": creator_public(c),
            }
        b = obj.brand
        return {
            "type": "brand",
            "id": str(b.id),
            "name": b.name,
            "image": b.logo_url or None,
            "profile": brand_public(b),
        }

    def get_lastMessage(self, obj: Conversation) -> dict[str, Any] | None:  # noqa: N802
        if obj.last_message_at is None:
            return None
        return {
            "text": getattr(obj, "last_body", "") or "",
            "hasAttachment": bool(getattr(obj, "last_attachment_id", None)),
            "fromMe": str(getattr(obj, "last_sender_id", "")) == str(self._me().pk),
            "sentAt": obj.last_message_at,
            "sentDate": ist_date(obj.last_message_at).isoformat(),
            "sentTime": ist_time(obj.last_message_at),
        }

    def get_unread(self, obj: Conversation) -> int:
        return getattr(obj, "unread", 0)

    def get_updatedAt(self, obj: Conversation) -> Any:  # noqa: N802
        return obj.last_message_at or obj.created_at


class ConversationOpenInput(StrictSerializer):
    """Brands send creatorId; creators send brandId."""

    creatorId = serializers.UUIDField(required=False)  # noqa: N815
    brandId = serializers.UUIDField(required=False)  # noqa: N815


class MessageInput(StrictSerializer):
    text = serializers.CharField(max_length=4000, required=False, allow_blank=True, default="", trim_whitespace=True)
    attachmentUploadId = serializers.UUIDField(required=False)  # noqa: N815

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("text") and not attrs.get("attachmentUploadId"):
            raise serializers.ValidationError("Send text, an attachment, or both.")
        return attrs
