from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity.permissions import IsBrandOrCreator
from apps.realtime.tickets import TICKET_TTL_SECONDS, issue_ticket


class RealtimeTicketView(APIView):
    """Trade your JWT for a single-use ticket, then open wss://<host>/ws/?ticket=<ticket> within 30s."""

    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(
        request=None,
        responses=inline_serializer(
            "RealtimeTicket",
            {
                "ticket": serializers.CharField(),
                "expiresIn": serializers.IntegerField(),
                "path": serializers.CharField(),
            },
        ),
    )
    def post(self, request: Request) -> Response:
        return Response({"ticket": issue_ticket(request.user.pk), "expiresIn": TICKET_TTL_SECONDS, "path": "/ws/"})
