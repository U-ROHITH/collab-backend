from datetime import date
from uuid import UUID

from django.db.models import Count
from django.db.models.functions import TruncDate
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.dates import IST, end_of_day_ist, start_of_day_ist
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
        qs = Message.objects.filter(conversation=conversation).select_related("attachment")
        if day := self.request.query_params.get("date"):  # calendar: jump to one day (IST)
            d = _parse_date(day)
            qs = qs.filter(created_at__gte=start_of_day_ist(d), created_at__lte=end_of_day_ist(d))
        return qs

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


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise DomainError("Use a date like 2026-11-20.", code="INVALID_DATE") from None


class ConversationDatesView(APIView):
    """Calendar support: the IST days that have messages, with counts. Optional ?month=YYYY-MM."""

    permission_classes = [IsBrandOrCreator]

    @extend_schema(
        responses=inline_serializer(
            "MessageDay", {"date": serializers.DateField(), "count": serializers.IntegerField()}, many=True
        )
    )
    def get(self, request: Request, conversation_id: UUID) -> Response:
        conversation = conversations_for(request.user).filter(pk=conversation_id).first()
        if conversation is None:
            raise NotFound()
        qs = Message.objects.filter(conversation=conversation)
        if month := request.query_params.get("month"):
            first = _parse_date(f"{month}-01")
            nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
            qs = qs.filter(created_at__gte=start_of_day_ist(first), created_at__lt=start_of_day_ist(nxt))
        days = (
            qs.annotate(day=TruncDate("created_at", tzinfo=IST))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        )
        return Response([{"date": d["day"].isoformat(), "count": d["count"]} for d in days])
