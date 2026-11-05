from typing import Any

from rest_framework import serializers

from apps.brands.models import Brand
from apps.conversations.models import Conversation, Message
from apps.core.serializers import StrictSerializer
from apps.creators.models import Creator


class MessageOutput(serializers.ModelSerializer):
    conversationId = serializers.UUIDField(source="conversation_id")  # noqa: N815
    senderId = serializers.UUIDField(source="sender_id")  # noqa: N815
    senderRole = serializers.CharField(source="sender_role")  # noqa: N815
    isRead = serializers.BooleanField(source="is_read")  # noqa: N815
    readAt = serializers.DateTimeField(source="read_at")  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Message
        fields = (
            "id",
            "conversationId",
            "senderId",
            "senderRole",
            "content",
            "isRead",
            "readAt",
            "createdAt",
        )


class ConversationParticipantBrandOutput(serializers.ModelSerializer):
    logoUrl = serializers.CharField(source="logo_url", allow_blank=True, required=False)  # noqa: N815

    class Meta:
        model = Brand
        fields = ("id", "name", "logoUrl")


class ConversationParticipantCreatorOutput(serializers.ModelSerializer):
    displayName = serializers.CharField(source="display_name")  # noqa: N815
    avatarUrl = serializers.SerializerMethodField()  # noqa: N815

    class Meta:
        model = Creator
        fields = ("id", "displayName", "avatarUrl")

    def get_avatarUrl(self, obj: Creator) -> str | None:  # noqa: N802
        url = obj.avatar_url or (obj.profile_picture.url if bool(obj.profile_picture) else None)
        if url:
            request = self.context.get("request")
            if request and not url.startswith("http://") and not url.startswith("https://"):
                return request.build_absolute_uri(url)
            return url
        return None


class ConversationOutput(serializers.ModelSerializer):
    brand = ConversationParticipantBrandOutput(read_only=True)
    creator = ConversationParticipantCreatorOutput(read_only=True)
    lastMessage = serializers.SerializerMethodField()  # noqa: N815
    unreadCount = serializers.SerializerMethodField()  # noqa: N815
    lastMessageAt = serializers.DateTimeField(source="last_message_at")  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815
    updatedAt = serializers.DateTimeField(source="updated_at")  # noqa: N815

    class Meta:
        model = Conversation
        fields = (
            "id",
            "brand",
            "creator",
            "lastMessage",
            "unreadCount",
            "lastMessageAt",
            "createdAt",
            "updatedAt",
        )

    def get_lastMessage(self, obj: Conversation) -> dict[str, Any] | None:  # noqa: N802
        msg = getattr(obj, "_prefetched_last_message", None)
        if msg is None:
            msg = obj.messages.order_by("-created_at").first()
        if msg:
            return MessageOutput(msg).data
        return None

    def get_unreadCount(self, obj: Conversation) -> int:  # noqa: N802
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            return obj.messages.filter(is_read=False).exclude(sender=request.user).count()
        return 0


class ConversationInput(StrictSerializer):
    creatorId = serializers.UUIDField(required=False)  # noqa: N815
    brandId = serializers.UUIDField(required=False)  # noqa: N815
    recipientId = serializers.UUIDField(required=False)  # noqa: N815
    creator = serializers.UUIDField(required=False)
    brand = serializers.UUIDField(required=False)
    content = serializers.CharField(max_length=5000, required=False, allow_blank=True)
    message = serializers.CharField(max_length=5000, required=False, allow_blank=True)


class MessageInput(StrictSerializer):
    content = serializers.CharField(max_length=5000, required=False, allow_blank=True)
    message = serializers.CharField(max_length=5000, required=False, allow_blank=True)
    text = serializers.CharField(max_length=5000, required=False, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        content = attrs.get("content") or attrs.get("message") or attrs.get("text")
        if not content or not content.strip():
            raise serializers.ValidationError({"content": ["Message content cannot be blank."]})
        attrs["content"] = content.strip()
        return attrs
