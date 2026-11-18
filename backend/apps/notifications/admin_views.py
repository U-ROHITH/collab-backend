from uuid import UUID

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import NotFound
from apps.identity.models import AdminRole
from apps.identity.permissions import RequireAdminRoles
from apps.notifications.models import Notification


class AdminNotificationOutput(serializers.ModelSerializer):
    isRead = serializers.SerializerMethodField()  # noqa: N815
    readAt = serializers.DateTimeField(source="read_at", allow_null=True)  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Notification
        fields = (
            "id",
            "type",
            "title",
            "body",
            "data",
            "isRead",
            "readAt",
            "createdAt",
        )

    def get_isRead(self, obj: Notification) -> bool:  # noqa: N802
        return obj.read_at is not None


class AdminNotificationUnreadCountResult(serializers.Serializer):
    unreadCount = serializers.IntegerField()  # noqa: N815


class AdminNotificationMarkAllResult(serializers.Serializer):
    markedCount = serializers.IntegerField()  # noqa: N815


@extend_schema(tags=["Admin - System"])
class AdminNotificationListView(APIView):
    """List notifications for the requesting admin."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]

    @extend_schema(
        parameters=[
            OpenApiParameter("unreadOnly", bool, description="Filter only unread notifications", required=False),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor ID", required=False),
        ],
        responses={200: AdminNotificationOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        qs = Notification.objects.filter(user=request.user)

        unread_only = request.query_params.get("unreadOnly", "").lower() in ("true", "1")
        if unread_only:
            qs = qs.filter(read_at__isnull=True)

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except (ValueError, TypeError):
            limit = 20

        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                c_item = Notification.objects.filter(pk=UUID(cursor), user=request.user).first()
                if c_item:
                    qs = qs.filter(created_at__lt=c_item.created_at)
            except (ValueError, TypeError):
                pass

        notifications = list(qs.order_by("-created_at")[:limit])
        return Response(AdminNotificationOutput(notifications, many=True).data)


@extend_schema(tags=["Admin - System"])
class AdminNotificationUnreadCountView(APIView):
    """Get count of unread notifications for the requesting admin."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]

    @extend_schema(responses={200: AdminNotificationUnreadCountResult})
    def get(self, request: Request) -> Response:
        count = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
        return Response({"unreadCount": count})


@extend_schema(tags=["Admin - System"])
class AdminNotificationMarkReadView(APIView):
    """Mark a notification as read."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]

    @extend_schema(request=None, responses={200: AdminNotificationOutput})
    def post(self, request: Request, id: UUID) -> Response:
        n = Notification.objects.filter(pk=id, user=request.user).first()
        if n is None:
            raise NotFound("Notification not found.")
        if n.read_at is None:
            n.read_at = timezone.now()
            n.save(update_fields=["read_at", "updated_at"])
        return Response(AdminNotificationOutput(n).data)


@extend_schema(tags=["Admin - System"])
class AdminNotificationMarkAllReadView(APIView):
    """Mark all notifications for the requesting admin as read."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]

    @extend_schema(request=None, responses={200: AdminNotificationMarkAllResult})
    def post(self, request: Request) -> Response:
        now = timezone.now()
        updated = Notification.objects.filter(user=request.user, read_at__isnull=True).update(
            read_at=now, updated_at=now
        )
        return Response({"markedCount": updated})
