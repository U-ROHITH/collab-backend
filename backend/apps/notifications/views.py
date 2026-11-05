from uuid import UUID

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications import services
from apps.notifications.models import Notification


class NotificationOutput(serializers.ModelSerializer):
    read = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Notification
        fields = ("id", "type", "title", "body", "data", "read", "createdAt")

    def get_read(self, obj: Notification) -> bool:
        return obj.read_at is not None


class NotificationListView(ListAPIView):
    serializer_class = NotificationOutput

    def get_queryset(self):  # type: ignore[no-untyped-def]
        qs = Notification.objects.filter(user=self.request.user)
        if self.request.query_params.get("unread") == "true":
            qs = qs.filter(read_at__isnull=True)
        return qs


class NotificationReadView(APIView):
    @extend_schema(request=None, responses=NotificationOutput)
    def patch(self, request: Request, notification_id: UUID) -> Response:
        return Response(NotificationOutput(services.mark_read(user=request.user, notification_id=notification_id)).data)


class NotificationReadAllView(APIView):
    @extend_schema(request=None, responses=inline_serializer("ReadAll", {"updated": serializers.IntegerField()}))
    def post(self, request: Request) -> Response:
        return Response({"updated": services.mark_all_read(user=request.user)})


class UnreadCountView(APIView):
    @extend_schema(responses=inline_serializer("UnreadCount", {"unread": serializers.IntegerField()}))
    def get(self, request: Request) -> Response:
        return Response({"unread": Notification.objects.filter(user=request.user, read_at__isnull=True).count()})
