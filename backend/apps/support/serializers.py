from typing import Any

from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.orders.models import Order
from apps.support.models import (
    SupportMessage,
    SupportTicket,
    TicketPriority,
    TicketStatus,
    TicketTopic,
)
from apps.uploads.models import Upload


class SupportMessageOutput(serializers.ModelSerializer):
    ticketId = serializers.UUIDField(source="ticket_id")  # noqa: N815
    authorType = serializers.CharField(source="author_type")  # noqa: N815
    authorId = serializers.UUIDField(source="author_user_id", allow_null=True)  # noqa: N815
    internal = serializers.BooleanField()
    text = serializers.CharField()
    attachmentIds = serializers.ListField(source="attachment_ids")  # noqa: N815
    attachments = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = SupportMessage
        fields = (
            "id",
            "ticketId",
            "authorType",
            "authorId",
            "internal",
            "text",
            "attachmentIds",
            "attachments",
            "createdAt",
        )

    def get_attachments(self, obj: SupportMessage) -> list[dict[str, Any]]:
        if not obj.attachment_ids:
            return []
        uploads = Upload.objects.filter(id__in=obj.attachment_ids)
        return [
            {
                "uploadId": str(u.id),
                "fileName": u.original_filename or "",
                "contentType": u.content_type or u.declared_content_type,
                "size": u.size_bytes or u.declared_size,
                "url": u.public_url,
            }
            for u in uploads
        ]


class SupportTicketOutput(serializers.ModelSerializer):
    ticketNumber = serializers.CharField(source="formatted_number", read_only=True)  # noqa: N815
    userId = serializers.UUIDField(source="user_id")  # noqa: N815
    orderId = serializers.UUIDField(source="order_id", allow_null=True)  # noqa: N815
    assigneeId = serializers.UUIDField(source="assignee_id", allow_null=True)  # noqa: N815
    unreadForUser = serializers.IntegerField(source="unread_for_user")  # noqa: N815
    lastUserMessageAt = serializers.DateTimeField(source="last_user_message_at", allow_null=True)  # noqa: N815
    lastAdminMessageAt = serializers.DateTimeField(source="last_admin_message_at", allow_null=True)  # noqa: N815
    resolvedAt = serializers.DateTimeField(source="resolved_at", allow_null=True)  # noqa: N815
    closedAt = serializers.DateTimeField(source="closed_at", allow_null=True)  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815
    updatedAt = serializers.DateTimeField(source="updated_at")  # noqa: N815
    lastMessage = serializers.SerializerMethodField()  # noqa: N815

    class Meta:
        model = SupportTicket
        fields = (
            "id",
            "number",
            "ticketNumber",
            "userId",
            "role",
            "topic",
            "orderId",
            "subject",
            "status",
            "priority",
            "assigneeId",
            "unreadForUser",
            "lastUserMessageAt",
            "lastAdminMessageAt",
            "resolvedAt",
            "closedAt",
            "createdAt",
            "updatedAt",
            "lastMessage",
        )

    def get_lastMessage(self, obj: SupportTicket) -> dict[str, Any] | None:  # noqa: N802
        request = self.context.get("request")
        is_admin = request and (
            getattr(request.user, "is_staff", False) or getattr(request.user, "role", None) == "admin"
        )
        msgs = obj.messages.all()
        if not is_admin:
            msgs = msgs.filter(internal=False)
        last_msg = msgs.order_by("-created_at").first()
        if not last_msg:
            return None
        return {
            "id": str(last_msg.id),
            "authorType": last_msg.author_type,
            "text": last_msg.text[:200],
            "createdAt": last_msg.created_at.isoformat() if last_msg.created_at else None,
        }


class SupportTicketDetailOutput(SupportTicketOutput):
    messages = serializers.SerializerMethodField()
    order = serializers.SerializerMethodField()

    class Meta(SupportTicketOutput.Meta):
        fields = SupportTicketOutput.Meta.fields + ("messages", "order")

    def get_messages(self, obj: SupportTicket) -> list[dict[str, Any]]:
        request = self.context.get("request")
        is_admin = request and (
            getattr(request.user, "is_staff", False) or getattr(request.user, "role", None) == "admin"
        )
        msgs = obj.messages.all()
        if not is_admin:
            msgs = msgs.filter(internal=False)
        return SupportMessageOutput(msgs.order_by("created_at"), many=True, context=self.context).data

    def get_order(self, obj: SupportTicket) -> dict[str, Any] | None:
        if not obj.order:
            return None
        return {
            "id": str(obj.order.id),
            "title": obj.order.title,
            "status": obj.order.status,
            "amountPaise": obj.order.subtotal_paise,
        }


class AdminSupportTicketDetailOutput(SupportTicketDetailOutput):
    userSummary = serializers.SerializerMethodField()  # noqa: N815
    orderSummary = serializers.SerializerMethodField()  # noqa: N815

    class Meta(SupportTicketDetailOutput.Meta):
        fields = SupportTicketDetailOutput.Meta.fields + ("userSummary", "orderSummary")

    def get_userSummary(self, obj: SupportTicket) -> dict[str, Any]:  # noqa: N802
        u = obj.user
        creator = getattr(u, "creator", None)
        brand = getattr(u, "brands", None).first() if hasattr(u, "brands") else None
        display_name = ""
        if creator and getattr(creator, "display_name", None):
            display_name = creator.display_name
        elif brand and getattr(brand, "name", None):
            display_name = brand.name
        elif u.email:
            display_name = u.email.split("@")[0]

        from django.db.models import Q

        total_orders = Order.objects.filter(Q(brand__owner=u) | Q(creator__user=u)).count()
        return {
            "id": str(u.id),
            "email": u.email,
            "phone": u.phone,
            "role": u.role,
            "displayName": display_name,
            "totalOrders": total_orders,
        }

    def get_orderSummary(self, obj: SupportTicket) -> dict[str, Any] | None:  # noqa: N802
        if not obj.order:
            return None
        o = obj.order
        return {
            "id": str(o.id),
            "title": o.title,
            "status": o.status,
            "amountPaise": o.subtotal_paise,
            "brandName": o.brand.name if o.brand else "",
            "creatorName": o.creator.display_name if o.creator else "",
            "createdAt": o.created_at.isoformat() if o.created_at else None,
        }


# --- Inputs ---


class TicketCreateInput(StrictSerializer):
    topic = serializers.ChoiceField(choices=TicketTopic.values)
    orderId = serializers.UUIDField(required=False, allow_null=True)  # noqa: N815
    subject = serializers.CharField(min_length=5, max_length=120)
    message = serializers.CharField(min_length=1, max_length=5000)
    attachmentUploadIds = serializers.ListField(  # noqa: N815
        child=serializers.UUIDField(),
        required=False,
        default=list,
        max_length=3,
    )

    def validate_subject(self, value: str) -> str:
        s = value.strip()
        if len(s) < 5 or len(s) > 120:
            raise serializers.ValidationError("Subject must be between 5 and 120 characters.")
        return s

    def validate_message(self, value: str) -> str:
        s = value.strip()
        if not s or len(s) > 5000:
            raise serializers.ValidationError("Message must be between 1 and 5000 characters.")
        return s


class UserMessageInput(StrictSerializer):
    message = serializers.CharField(min_length=1, max_length=5000)
    attachmentUploadIds = serializers.ListField(  # noqa: N815
        child=serializers.UUIDField(),
        required=False,
        default=list,
        max_length=3,
    )

    def validate_message(self, value: str) -> str:
        s = value.strip()
        if not s or len(s) > 5000:
            raise serializers.ValidationError("Message must be between 1 and 5000 characters.")
        return s


class AdminReplyInput(StrictSerializer):
    message = serializers.CharField(min_length=1, max_length=5000)
    attachmentUploadIds = serializers.ListField(  # noqa: N815
        child=serializers.UUIDField(),
        required=False,
        default=list,
        max_length=3,
    )

    def validate_message(self, value: str) -> str:
        s = value.strip()
        if not s or len(s) > 5000:
            raise serializers.ValidationError("Message must be between 1 and 5000 characters.")
        return s


class AdminNoteInput(StrictSerializer):
    note = serializers.CharField(min_length=1, max_length=5000, required=False)
    message = serializers.CharField(min_length=1, max_length=5000, required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        text = attrs.get("note") or attrs.get("message")
        if not text or not text.strip():
            raise serializers.ValidationError({"note": ["Note content is required."]})
        attrs["note"] = text.strip()
        return attrs


class AdminAssignInput(StrictSerializer):
    adminId = serializers.CharField(allow_null=True, required=True)  # noqa: N815


class AdminTicketPatchInput(StrictSerializer):
    priority = serializers.ChoiceField(choices=TicketPriority.values, required=False)
    status = serializers.ChoiceField(choices=TicketStatus.values, required=False)
