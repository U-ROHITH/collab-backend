from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import DomainError, NotFound
from apps.core.pagination import EnvelopeCursorPagination
from apps.identity.models import Role
from apps.identity.permissions import IsBrandOrCreator
from apps.messaging import services
from apps.messaging.models import Message
from apps.messaging.selectors import conversations_for, conversations_with_summary
from apps.messaging.serializers import ConversationOpenInput, ConversationOutput, MessageInput, MessageOutput


class ConversationPagination(EnvelopeCursorPagination):
    ordering = ("-updated_at", "-id")


class ConversationListCreateView(ListAPIView):
    permission_classes = [IsBrandOrCreator]
    serializer_class = ConversationOutput
    pagination_class = ConversationPagination
    throttle_scope = "writes"

    def get_queryset(self):  # type: ignore[no-untyped-def]
        return conversations_with_summary(self.request.user)

    @extend_schema(request=ConversationOpenInput, responses={201: ConversationOutput})
    def post(self, request: Request) -> Response:
        data = ConversationOpenInput(data=request.data)
        data.is_valid(raise_exception=True)
        key = "creatorId" if request.user.role == Role.BRAND else "brandId"
        counterpart = data.validated_data.get(key)
        if counterpart is None:
            raise DomainError(f"{key} is required.", code="VALIDATION_ERROR")
        conversation = services.open_conversation(user=request.user, counterpart_id=counterpart)
        conversation = conversations_with_summary(request.user).get(pk=conversation.pk)
        return Response(
            ConversationOutput(conversation, context={"request": request}).data, status=status.HTTP_201_CREATED
        )


class ConversationDetailView(APIView):
    permission_classes = [IsBrandOrCreator]

    @extend_schema(responses=ConversationOutput)
    def get(self, request: Request, conversation_id: UUID) -> Response:
        conversation = conversations_with_summary(request.user).filter(pk=conversation_id).first()
        if conversation is None:
            raise NotFound()
        return Response(ConversationOutput(conversation, context={"request": request}).data)


class MessagePagination(EnvelopeCursorPagination):
    """Newest first; pass meta.nextCursor to load older messages."""

    page_size = 30


class MessageListCreateView(ListAPIView):
    permission_classes = [IsBrandOrCreator]
    serializer_class = MessageOutput
    pagination_class = MessagePagination

    def get_throttles(self):  # type: ignore[no-untyped-def]
        self.throttle_scope = "messages" if self.request.method == "POST" else "writes"
        return super().get_throttles()

    def get_queryset(self):  # type: ignore[no-untyped-def]
        conversation = conversations_for(self.request.user).filter(pk=self.kwargs["conversation_id"]).first()
        if conversation is None:
            raise NotFound()
        return Message.objects.filter(conversation=conversation).select_related("attachment")

    @extend_schema(request=MessageInput, responses={201: MessageOutput})
    def post(self, request: Request, conversation_id: UUID) -> Response:
        data = MessageInput(data=request.data)
        data.is_valid(raise_exception=True)
        message = services.send_message(
            user=request.user,
            conversation_id=conversation_id,
            text=data.validated_data["text"],
            attachment_upload_id=data.validated_data.get("attachmentUploadId"),
        )
        return Response(MessageOutput(message, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ConversationReadView(APIView):
    permission_classes = [IsBrandOrCreator]

    @extend_schema(request=None, responses={204: None})
    def post(self, request: Request, conversation_id: UUID) -> Response:
        services.mark_read(user=request.user, conversation_id=conversation_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
