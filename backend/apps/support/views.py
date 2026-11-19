from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import NotFound
from apps.core.pagination import EnvelopeCursorPagination
from apps.identity.permissions import IsBrandOrCreator
from apps.support import services
from apps.support.models import SupportTicket
from apps.support.serializers import (
    SupportMessageOutput,
    SupportTicketDetailOutput,
    SupportTicketOutput,
    TicketCreateInput,
    UserMessageInput,
)


class TicketCursorPagination(EnvelopeCursorPagination):
    ordering = ("-updated_at", "-id")


class TicketListCreateView(ListAPIView):
    """GET /v1/support/tickets/ — list user's tickets.
    POST /v1/support/tickets/ — create a new ticket.
    """

    permission_classes = [IsBrandOrCreator]
    pagination_class = TicketCursorPagination
    serializer_class = SupportTicketOutput
    throttle_scope = "writes"

    def get_queryset(self):
        qs = (
            SupportTicket.objects.filter(user=self.request.user)
            .select_related("order")
            .prefetch_related("messages")
        )
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    @extend_schema(request=TicketCreateInput, responses={201: SupportTicketDetailOutput})
    def post(self, request: Request) -> Response:
        data = TicketCreateInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        ticket = services.create_ticket(
            user=request.user,
            topic=v["topic"],
            subject=v["subject"],
            message=v["message"],
            order_id=v.get("orderId"),
            attachment_upload_ids=v.get("attachmentUploadIds"),
        )
        return Response(
            SupportTicketDetailOutput(ticket, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class TicketDetailView(APIView):
    """GET /v1/support/tickets/{id}/ — view ticket detail & messages, reset unread counter."""

    permission_classes = [IsBrandOrCreator]

    @extend_schema(responses={200: SupportTicketDetailOutput})
    def get(self, request: Request, ticket_id: UUID) -> Response:
        ticket = (
            SupportTicket.objects.filter(pk=ticket_id, user=request.user)
            .select_related("order")
            .prefetch_related("messages")
            .first()
        )
        if not ticket:
            raise NotFound("Ticket not found.")

        if ticket.unread_for_user > 0:
            ticket.unread_for_user = 0
            ticket.save(update_fields=["unread_for_user", "updated_at"])

        return Response(SupportTicketDetailOutput(ticket, context={"request": request}).data)


class TicketMessageCreateView(APIView):
    """POST /v1/support/tickets/{id}/messages/ — send user message on ticket."""

    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(request=UserMessageInput, responses={201: SupportMessageOutput})
    def post(self, request: Request, ticket_id: UUID) -> Response:
        data = UserMessageInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        msg = services.add_user_message(
            user=request.user,
            ticket_id=ticket_id,
            message=v["message"],
            attachment_upload_ids=v.get("attachmentUploadIds"),
        )
        return Response(
            SupportMessageOutput(msg, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class TicketResolveView(APIView):
    """POST /v1/support/tickets/{id}/resolve/ — user marks ticket resolved."""

    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(responses={200: SupportTicketDetailOutput})
    def post(self, request: Request, ticket_id: UUID) -> Response:
        ticket = services.resolve_ticket(user=request.user, ticket_id=ticket_id)
        return Response(
            SupportTicketDetailOutput(ticket, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class TicketReopenView(APIView):
    """POST /v1/support/tickets/{id}/reopen/ — user reopens resolved ticket (within 7 days)."""

    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(responses={200: SupportTicketDetailOutput})
    def post(self, request: Request, ticket_id: UUID) -> Response:
        ticket = services.reopen_ticket(user=request.user, ticket_id=ticket_id)
        return Response(
            SupportTicketDetailOutput(ticket, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )
