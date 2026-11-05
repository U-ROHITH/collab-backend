from typing import Any
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.conversations import services
from apps.conversations.models import Conversation
from apps.conversations.serializers import (
    ConversationInput,
    ConversationOutput,
    MessageInput,
    MessageOutput,
)
from apps.core.exceptions import NotFound
from apps.identity.models import Role
from apps.identity.permissions import IsBrandOrCreator


class ConversationListCreateView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(responses=ConversationOutput(many=True))
    def get(self, request: Request) -> Response:
        user = request.user
        if user.role == Role.BRAND and hasattr(user, "brand"):
            qs = Conversation.objects.filter(brand=user.brand)
        elif user.role == Role.CREATOR and hasattr(user, "creator"):
            qs = Conversation.objects.filter(creator=user.creator)
        else:
            return Response([])

        qs = qs.select_related("brand", "creator").order_by("-updated_at")
        return Response(ConversationOutput(qs, many=True, context={"request": request}).data)

    @extend_schema(request=ConversationInput, responses={201: ConversationOutput})
    def post(self, request: Request) -> Response:
        data = ConversationInput(data=request.data)
        data.is_valid(raise_exception=True)
        validated = data.validated_data

        target_creator_id = validated.get("creatorId") or validated.get("creator") or validated.get("recipientId")
        target_brand_id = validated.get("brandId") or validated.get("brand") or validated.get("recipientId")

        conversation, _ = services.get_or_create_conversation(
            user=request.user,
            target_creator_id=target_creator_id,
            target_brand_id=target_brand_id,
        )

        initial_message = (validated.get("content") or validated.get("message") or "").strip()
        if initial_message:
            services.send_message(
                conversation=conversation,
                sender_user=request.user,
                content=initial_message,
            )

        return Response(
            ConversationOutput(conversation, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ConversationMessagesView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    def get_conversation(self, conversation_id: UUID, user: Any) -> Conversation:
        try:
            conv = Conversation.objects.select_related("brand", "creator").get(pk=conversation_id)
        except Conversation.DoesNotExist:
            raise NotFound("Conversation not found.") from None

        is_brand = hasattr(user, "brand") and conv.brand_id == user.brand.id
        is_creator = hasattr(user, "creator") and conv.creator_id == user.creator.id
        if not is_brand and not is_creator:
            raise NotFound("Conversation not found.")
        return conv

    @extend_schema(responses=MessageOutput(many=True))
    def get(self, request: Request, conversation_id: UUID) -> Response:
        conv = self.get_conversation(conversation_id, request.user)
        services.mark_messages_as_read(conversation=conv, user=request.user)
        messages = conv.messages.all().order_by("created_at")
        return Response(MessageOutput(messages, many=True).data)

    @extend_schema(request=MessageInput, responses={201: MessageOutput})
    def post(self, request: Request, conversation_id: UUID) -> Response:
        conv = self.get_conversation(conversation_id, request.user)
        data = MessageInput(data=request.data)
        data.is_valid(raise_exception=True)
        msg = services.send_message(
            conversation=conv,
            sender_user=request.user,
            content=data.validated_data["content"],
        )
        return Response(MessageOutput(msg).data, status=status.HTTP_201_CREATED)
